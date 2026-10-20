# DATUMOJ 常用命令。uv 先在 PATH 里找，找不到再用 Homebrew 的路径；可用环境变量 UV 覆盖。
UV ?= $(shell command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)
T ?= tests
SOURCE ?=
LIMIT ?=

.PHONY: check test lint crawl parse index eval mcp

# 调度器验收用：make check T=tests/core/test_models.py
check:
	$(UV) run --frozen pytest -q $(T)

test:
	$(UV) run --frozen pytest -q

lint:
	$(UV) run --frozen ruff check src tests

crawl:
	$(UV) run --frozen datumoj crawl $(SOURCE) $(if $(LIMIT),--limit $(LIMIT))

parse:
	$(UV) run --frozen datumoj parse $(SOURCE)

index:
	$(UV) run --frozen datumoj index

eval:
	$(UV) run --frozen datumoj eval --gate

mcp:
	$(UV) run --frozen datumoj mcp
