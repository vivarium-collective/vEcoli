OUTDIR := "out"
.DEFAULT_GOAL := help

.PHONY: test_ecocyc
test_ecocyc:
	@rm -rf "out/transforms/sms_multiseed_multigen_ecocyc_transform"; \
	time uv run --no-cache --env-file .env runscripts/analysis.py --config configs/data_transformation_ecocyc.json

.PHONY: test
test:
	@make test_ecocyc; \
	uv run pytest -s /Users/alexanderpatrie/sms/vecoli_fork/ecoli/library/transform_utils.py


.PHONY: setd4
setd4:
	@rm -rf /Users/alexanderpatrie/sms/vecoli_fork/out/wcecoli_figure2_setD4 && uv run runscripts/workflow.py --config /Users/alexanderpatrie/sms/vecoli_fork/configs/wcecoli_figure2_setD4.json

.PHONY: transform
transform:
	@uv run --no-cache ./runscripts/analysis.py --config $(config)

.PHONY: transform_setd4
transform_setd4:
	@make transform config=/Users/alexanderpatrie/sms/vecoli_fork/configs/wcecoli_figure2_setD4_transform.json

.PHONY: ecocyc_tables
ecocyc_tables:
	@make transform config=/Users/alexanderpatrie/sms/vecoli_fork/configs/wcecoli_figure2_setD4_transform_ecocyc.json