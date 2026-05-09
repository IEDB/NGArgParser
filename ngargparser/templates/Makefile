SHELL := /bin/bash

# Makefile for ngargparser projects

.PHONY: build build-verbose clean

build:
	@if [ -f "./scripts/core/build.sh" ]; then \
		cd scripts/core && ./build.sh --progress; \
	else \
		echo "Error: scripts/core/build.sh not found. Run 'cli sync' if upgrading from a pre-scripts/core layout."; \
		exit 1; \
	fi

build-verbose:
	@if [ -f "./scripts/core/build.sh" ]; then \
		cd scripts/core && ./build.sh; \
	else \
		echo "Error: scripts/core/build.sh not found. Run 'cli sync' if upgrading from a pre-scripts/core layout."; \
		exit 1; \
	fi

clean:
	@if [ -f "./scripts/core/build.sh" ]; then \
		rm -rf build; \
		echo "Build directory cleaned."; \
	else \
		echo "Error: scripts/core/build.sh not found. Run 'cli sync' if upgrading from a pre-scripts/core layout."; \
		exit 1; \
	fi