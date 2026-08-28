import pandas as pd
df = pd.read_parquet("data/raw/onchain/l2_tvl_arbitrum.parquet")
diffs = df["timestamp"].diff()
gap_idx = diffs.idxmax()
print(df.loc[gap_idx-1:gap_idx+1])