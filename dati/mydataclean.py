import pandas as pd

df = pd.read_parquet("data/raw/onchain/l2_tvl_arbitrum.parquet")
df = df[df["tvl_usd"] > 0].reset_index(drop=True)
df.to_parquet("data/raw/onchain/l2_tvl_arbitrum.parquet", index=False)