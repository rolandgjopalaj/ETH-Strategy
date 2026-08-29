import pandas as pd
trades = pd.read_parquet("data/processed/backtest_trades.parquet")
print(trades.sort_values("return_pct", ascending=False).head(8)[["entry_date","exit_date","return_pct","entry_reason","exit_reason"]])
print("\nPer entry_reason:")
print(trades.groupby("entry_reason")["return_pct"].agg(["count","mean","sum"]))