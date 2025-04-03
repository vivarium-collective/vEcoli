.PHONY: commit
commit:
	@git commit -m "$(m)" --no-verify

.PHONY: push
push:
	@git push