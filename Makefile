# DATUMOJ 常用命令。uv 先在 PATH 里找，找不到再用 Homebrew 的路径；可用环境变量 UV 覆盖。
UV ?= $(shell command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)
T ?= tests
SOURCE ?=
LIMIT ?=

.PHONY: check test lint crawl parse index eval mcp

# 主仓库的 .venv（在 git worktree 里也能找到）：uv 用不了时（例如 codex 沙箱写不了 uv 缓存）拿它只读地跑测试
GIT_COMMON := $(shell git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
FALLBACK_PY ?= $(if $(GIT_COMMON),$(dir $(GIT_COMMON)).venv/bin/python)

# 调度器验收用：make check T=tests/core/test_models.py
check:
	@if $(UV) run --frozen python -c "" >/dev/null 2>&1; then \
		$(UV) run --frozen pytest -q $(T); \
	elif [ -x "$(FALLBACK_PY)" ]; then \
		echo "make check: uv 不可用（可能在沙箱里），改用 $(FALLBACK_PY)" >&2; \
		PYTHONPATH="$(CURDIR)/src$${PYTHONPATH:+:$$PYTHONPATH}" "$(FALLBACK_PY)" -m pytest -q $(T); \
	else \
		echo "make check: uv 不可用，也找不到主仓库的 .venv（先在主仓库运行一次 make test）" >&2; exit 2; \
	fi

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
