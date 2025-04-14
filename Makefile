.PHONY: clean
clean:
	@uv cache clean

.PHONY: sync
sync:
	@[ -n "$(f)" ] && uv sync --frozen || uv sync

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