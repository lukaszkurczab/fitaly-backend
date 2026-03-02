.PHONY: lint typecheck

lint:
	@if [ ! -x .venv/bin/ruff ]; then \
		echo "ruff is not installed in .venv. Run: .venv/bin/pip install -r requirements.txt"; \
		exit 1; \
	fi
	.venv/bin/ruff check .

typecheck:
	@if [ ! -x .venv/bin/pyright ]; then \
		echo "pyright is not installed in .venv. Run: .venv/bin/pip install -r requirements.txt"; \
		exit 1; \
	fi
	.venv/bin/pyright
