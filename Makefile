.PHONY: build wheel-debug clean-build release

# Build distribution
build: clean-build
	python3 -m pip install -q build
	python3 -m build

# Remove in-tree setuptools artifacts (stale build/ + egg-info causes Errno 17: File exists on bdist_wheel).
clean-build:
	rm -rf "$(CURDIR)/build" "$(CURDIR)/aioprom.egg-info"

# Verbose wheel build using ./.venv (no PEP 517 isolation → full setuptools logs). Setup once:
#   python3 -m venv .venv && .venv/bin/python -m pip install -U pip
wheel-debug: clean-build
	@test -x "$(CURDIR)/.venv/bin/python" || { \
		echo >&2 "No .venv/bin/python. Create it and upgrade pip, e.g.:"; \
		echo >&2 "  python3 -m venv .venv && .venv/bin/python -m pip install -U pip"; \
		exit 1; \
	}
	"$(CURDIR)/.venv/bin/python" -m pip install -U pip "setuptools>=77" wheel build prometheus-client
	mkdir -p "$(CURDIR)/dist"
	"$(CURDIR)/.venv/bin/python" -m pip wheel -vv --no-build-isolation -w "$(CURDIR)/dist" "$(CURDIR)"

# Trigger pre-commit version bump and autopublish
release:
	git commit --allow-empty -m "new release"
	git push
