.PHONY: data fetch-data model sim serve test

data:   ## fetch raw data and build data/processed/*.parquet (Person A)
	python -m pipeline.fetch
	python -m pipeline.reconstruct
	python -m pipeline.residual

model:  ## train forecast, price forecast, forecast skill by days ahead, then the dispatcher backtest (Person A). Order matters:
        ## train rewrites model_metrics.json, and the scorecard's money test reads price_forecast.parquet
	python -m pipeline.train
	python -m pipeline.price
	python -m pipeline.lead_skill
	python -m dispatch.scorecard

# without gh on PATH (Windows): make fetch-data GH="/c/Program Files/GitHub CLI/gh.exe"
GH ?= gh
fetch-data:  ## download the latest processed-data release and unzip its saved models (#68)
	"$(GH)" release download -D data/processed --clobber -p '*'
	python -c "import os, zipfile; p = 'data/processed/models.zip'; os.path.exists(p) and zipfile.ZipFile(p).extractall('data/processed')"

sim:    ## run the historical calibration check (Person B)
	python -m sim.calibrate

serve:  ## start the dashboard at http://localhost:8000 (Person B)
	uvicorn app.server:app --reload --port 8000

test:
	pytest -q
