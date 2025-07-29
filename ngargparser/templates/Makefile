SHELL := /bin/bash

# download all dependencies and build a tar archive
.PHONY: build
build:
	@if [ -f "./packaging/build.sh" ]; then \
		cd packaging && ./build.sh; \
	elif [ -f "./build.sh" ]; then \
		./build.sh; \
	else \
		echo "Error: build.sh not found in packaging/ or current directory"; \
		exit 1; \
	fi

# clean the build directory
.PHONY: clean
clean:
	@if [ -f "./packaging/build.sh" ]; then \
		rm -rf build; \
	else \
		rm -rf build; \
	fi