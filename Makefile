.PHONY: data model sim serve test

data:   ## fetch raw data and build data/processed/*.parquet (Person A)
	python -m pipeline.fetch
	python -m pipeline.reconstruct
	python -m pipeline.residual

model:  ## train forecast, price forecast, then the dispatcher backtest (Person A). Order matters:
        ## train rewrites model_metrics.json, and the scorecard's money test reads price_forecast.parquet
	python -m pipeline.train
	python -m pipeline.price
	python -m dispatch.scorecard

sim:    ## run the historical calibration check (Person B)
	python -m sim.calibrate

serve:  ## start the dashboard at http://localhost:8000 (Person B)
	uvicorn app.server:app --reload --port 8000

test:
	pytest -q
