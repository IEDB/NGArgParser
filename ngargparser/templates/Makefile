SHELL := /bin/bash

# Makefile for ngargparser projects

.PHONY: build clean

build:
	@if [ -f "./scripts/build.sh" ]; then \
		cd scripts && ./build.sh; \
	else \
		echo "Error: build.sh not found in scripts/ or current directory"; \
		exit 1; \
	fi

clean:
	@if [ -f "./scripts/build.sh" ]; then \
		rm -rf build; \
		echo "Build directory cleaned."; \
	else \
		echo "Error: build.sh not found in scripts/ or current directory"; \
		exit 1; \
	fi