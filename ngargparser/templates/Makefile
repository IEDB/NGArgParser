SHELL := /bin/bash

# Makefile for ngargparser tools. build.sh runs with bash, so a lost exec
# bit on it (a copy, an unzip, a Windows checkout) does not stop the build.

.PHONY: build build-verbose clean libs

# This Makefile, as make was given it: with make -f <path> from another
# folder, the tool folder is where it is, not the current folder.
NGARGPARSER_MAKEFILE := $(lastword $(MAKEFILE_LIST))

# Runs scripts/core/build.sh with the arguments in $(1). A build.sh saved with
# Windows line endings fails in bash with "$'\r': command not found" before
# it can say why, so it is checked here first, with the fix build.sh names
# for the files it reads (BSD sed, on macOS, reads \r as r: perl there).
define run_build_sh
	@if [ ! -f "./scripts/core/build.sh" ] && [ ! "$(NGARGPARSER_MAKEFILE)" -ef ./Makefile ]; then \
		echo "ERROR: run make in the tool folder (where Makefile is): cd $$(dirname "$(NGARGPARSER_MAKEFILE)") && make $@" >&2; \
		exit 1; \
	fi; \
	if [ ! -f "./scripts/core/build.sh" ]; then \
		echo "Error: scripts/core/build.sh not found. Run 'cli sync' if upgrading from a pre-scripts/core layout."; \
		exit 1; \
	fi; \
	if grep -q "$$(printf '\r')$$" ./scripts/core/build.sh; then \
		if [ "$$(uname)" = Darwin ]; then \
			fix="perl -pi -e 's/\\r\$$//' scripts/core/build.sh"; \
		else \
			fix='sed -i "s/\r$$//" scripts/core/build.sh'; \
		fi; \
		echo "ERROR: scripts/core/build.sh has Windows line endings (CRLF): convert it with '$$fix' (and add '*.sh text eol=lf' to .gitattributes)" >&2; \
		exit 1; \
	fi; \
	cd scripts/core && bash ./build.sh $(1)
endef

build:
	$(call run_build_sh,--progress)

build-verbose:
	$(call run_build_sh,)

# Fetches into libs/ what scripts/get_libs.sh fetches, as the first step of a
# build does (and with the same record, so the next build does not fetch it
# again): a run in the tool folder can then import it before the first build.
libs:
	$(call run_build_sh,--libs-only)

# Removes build/ under the build lock, so it never deletes the copy a running
# build is writing.
clean:
	$(call run_build_sh,--clean)
