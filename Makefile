.PHONY: data model sim serve test

data:   ## fetch raw data and build data/processed/*.parquet (Person A)
	python -m pipeline.fetch
	python -m pipeline.reconstruct
	python -m pipeline.residual

model:  ## train forecast, write forecast.parquet, run dispatcher backtest (Person A)
	python -m pipeline.train
	python -m dispatch.scorecard

sim:    ## run the historical calibration check (Person B)
	python -m sim.calibrate

serve:  ## start the dashboard at http://localhost:8000 (Person B)
	uvicorn app.server:app --reload --port 8000

test:
	pytest -q
