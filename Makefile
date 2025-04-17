.PHONY: clean
clean:
	@uv cache clean && rm uv.lock && uv lock

.PHONY: sync
sync:
	@[ -n "$(e)" ] && uv sync --frozen --all-extras || uv sync --frozen

.PHONY: commit
commit:
	@git commit -m "$(m)" --no-verify

.PHONY: push
push:
	@git push

.PHONY: add
add:
	@[ -n "$(files)" ] && git add "$(files)" || git add --all

.PHONY: test
test:
	@[ -n "$(dest)" ] && pytest -s "$(dest)" || pytest -s

.PHONY: api
api:
	@uv run uvicorn api.gateway.app:app --reload --host 0.0.0.0 --port 8080