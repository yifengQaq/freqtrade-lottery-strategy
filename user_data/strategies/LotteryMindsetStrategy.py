class WeeklyBudgetController:
    pass

class LotteryMindsetStrategy:
    stoploss = -0.95
    leverage = 5

    def can_open_trade(self):
        return True

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
