#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: $0 <parquet_file>"
    exit 1
fi

# Check that it ends in .parquet
if [[ ! "$1" =~ \.parquet$ ]]; then
    echo "Error: Parquet file must end in .parquet"
    exit 1
fi

exec duckdb -c "COPY (SELECT * FROM read_json('/dev/stdin')) TO '$1'"