# Development

Install the build tools, then run the package check from the repository root:

```sh
python -m pip install -r requirements-dev.txt
python -B tools/check.py
```

This builds the source distribution and wheel, validates their metadata and
contents, and runs runtime and consumer typing tests against an independently
installed wheel. The tests use synthetic
child processes and do not require an Android device.

Shell integration tests require bash and `base64 -w 0`; they skip when these are
unavailable. Set `ADBLANE_TEST_SHELL` to select a bash executable. CI runs
`tools/check.py --fail-on-skip` so missing dependencies fail the check.

On macOS, prepare GNU coreutils before running the check:

```sh
brew install coreutils
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"
```

## Versions

Development takes place on `master`. The version in `src/adblane/_version.py`
supplies both package metadata and `adblane.__version__`; update it together with
`CHANGELOG.md`. Until 1.0, patch releases preserve the public API and breaking
changes require a minor version bump. Import public names from `adblane`;
underscored modules and attributes are internal.

## Contributing

Fork the repository and create a branch such as `fix/short-description` or
`feature/short-description`. Run `python -B tools/check.py --fail-on-skip`,
then open a pull request from your fork against `master` with a short explanation
of the change and how you tested it.

## Testing with a device

Install the package and supply a device serial from `adb devices`:

```sh
python -m pip install .
python -B tools/smoke_device.py --serial YOUR_DEVICE_SERIAL
```

The smoke test checks binary reads and error handling without modifying device
files. Use `--adb PATH` to select a different ADB executable.
