import numpy as np
import matplotlib.pyplot as plt
from functools import wraps
from lightgbm import LGBMClassifier
from sklearn.model_selection import cross_validate
from toads.eda import plot_time_series
from toads.image import Img
from toads.utils import conditional

from .feature_extraction import min_price_for_profit, make_buy_features, calc_cross_profit


class Buyer:
    """Decision mechanism for buying recommendations."""

    def learn_buy_recommendation(self, profit_threshold=0, interval='1min', periods=None, batches=1, verbose=True,
                                 draw_chart=True):
        """The complete pipeline to learn buy recommendation for stocks.

        Args:
            api (TinkoffAPI): API context object to perform operations with.
            verbose (bool): to print or not to print.

        Returns:
            float: value of buy recommendation for the last available stock price.
        """

        @conditional(verbose)
        @wraps(print)
        def prt(*args, **kws):
            """Print if condition is true."""
            print(*args, **kws)

        # Скачать данные
        data = self.api.get_stock_prices(interval=interval, periods=periods, batches=batches)
        assert data.shape[0] > 0, 'Data is empty. Try increasing periods count.'
        prt(f'Downloaded {data.shape[0]} rows.')
        prices = data.open.rename('price')

        # Отдельно сохраним последнюю цену
        current = data.close[-1]
        prt('Current price:', current)

        # Сформировать обучающую выборку
        train = make_buy_features(data, 'open', shift_windows=False)
        #         train = data[['open', 'close', 'high', 'low', 'volume']]
        # Отобрать текущее значение для предсказания
        X_current = train.iloc[[-1], :].drop(columns=['open', 'close', 'high', 'low', 'volume'])
        if self.policy in ('full', 'lookaround', 'lar'):
            X_current['future'] = 1

        # Crossjoin
        train_cross = calc_cross_profit(
            train,
            broker_commission=self.broker_commission,
            policy=self.policy,
            profit_threshold=profit_threshold
        ).drop(columns=['open', 'close', 'high', 'low', 'volume'])

        self.train_data = train_cross
        # if verbose:
        #     display(train_cross.tail(3))

        # Средняя вероятность прибыли по всей выборке с выбранной политикой
        prt(f'Overall profit chance: {train_cross.profit.mean():.2%}')

        # Обучение и предсказание рекомендации
        X, y = train_cross.drop(columns='profit'), train_cross.profit

        # Кросс-валидация
        try:
            rocauc = np.mean(cross_validate(self.model, X, y, scoring='roc_auc', cv=5)['test_score'])
            prt(f'ROC AUC score: {rocauc:.3f}')
        except:
            rocauc = None

        # Предсказание
        try:
            pred = self.model.fit(X, y).predict_proba(X_current)[0, 1]
        except ValueError:
            raise ValueError('profit_threshold argument may be too large.')
        prt(f'Buy recommendation: {pred:.3%}')

        # Рисуем контрольный график
        if draw_chart:
            self.draw_chart(prices, current,
                            title=f'Buy = {pred:.2%} for minimum profit = {profit_threshold} {self.api.instrument.currency}',
                            optional_feature=self.train_data.groupby('datetime').profit.mean().rename('Profit %'))

        return {'ticker': self.api.instrument.ticker,
                'time': X_current.index[0],
                'interval': interval,
                'periods': periods,
                'batches': batches,
                'price': current,
                'profit_threshold': profit_threshold,
                'buy': pred,
                'policy': self.policy,
                'roc_auc': rocauc}

    def get_current_price(self):
        """Get current price from api."""
        self.api.get_stock_prices(interval='1min', periods=5).close[-1]

    def draw_chart(self, prices, current_price, title=None, optional_feature=None):
        """Draw a chart to visualize green zone and current situation."""
        with Img(st=title, legend='f' if optional_feature is not None else 'a', ):
            # Зелёная зона - где продажа без убытка
            green_min = min_price_for_profit(current_price)
            plt.axhspan(green_min,
                        prices.max(), alpha=.2, color='g', label=f'Non-loss zone @ {green_min}+')
            Img.labels('Datetime', f'Price, {self.api.instrument.currency}')
            plot_time_series(prices, label=f'{self.api.instrument.name} ({self.api.instrument.ticker})')
            plt.axhline(current_price, color='orange', ls=':', label=f'Current price = {current_price}')

            if optional_feature is not None:
                plot_time_series(optional_feature, label=optional_feature.name, color='red', ax=plt.gca().twinx(),
                                 alpha=0.5)

    def __init__(self, api, policy='lookaround', model=LGBMClassifier(), broker_commission=0.003):
        self.api = api
        self.broker_commission = broker_commission
        self.model = model
        self.policy = policy


__all__ = ['Buyer']
