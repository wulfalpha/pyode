# SPDX-License-Identifier: GPL-2.0-or-later
"""Locating libmpv.

python-mpv finds the shared library with `ctypes.util.find_library`, which
only ever consults the dynamic loader's own search path.  That is enough on a
distribution that installs libmpv into a system library directory, and it is
not enough anywhere else:

* **macOS** -- Homebrew installs into `/opt/homebrew/lib` (Apple silicon) or
  `/usr/local/lib` (Intel), and `dyld` searches neither by default.  Whether
  `find_library("mpv")` happens to work comes down to what else has been
  installed on the machine, which is why the same pyode can run on one Mac and
  not on the next.
* **Windows** -- `find_library` walks `%PATH%` looking for the file, so it
  finds libmpv only if the DLL sits in a directory that is already on `%PATH%`.
  Scoop, Chocolatey and the official builds all install a statically linked
  `mpv.exe` and no DLL at all, so there is usually nothing to find.

So we look ourselves, in the places the platform's package managers actually
install to, and hand python-mpv an absolute path.  The point is that nobody
should have to set an environment variable to run a radio player.

`PYODE_LIBMPV` still overrides everything, for the cases we have not thought
of; it takes either the library file or the directory holding it.
"""

from __future__ import annotations

import ctypes.util
import logging
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from platformdirs import user_data_path

log = logging.getLogger(__name__)

ENV_VAR = "PYODE_LIBMPV"

if sys.platform == "win32":
    # python-mpv's own name list, plus the plain name some builds use.
    LIB_NAMES = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll", "libmpv.dll")
    _GLOBS = ("libmpv*.dll", "mpv-*.dll")
elif sys.platform == "darwin":
    LIB_NAMES = ("libmpv.2.dylib", "libmpv.dylib", "libmpv.1.dylib")
    _GLOBS = ("libmpv.*.dylib",)
else:
    LIB_NAMES = ("libmpv.so.2", "libmpv.so", "libmpv.so.1")
    _GLOBS = ("libmpv.so.*",)


class LibmpvNotFoundError(RuntimeError):
    """Raised when libmpv cannot be located or loaded."""


def bundled_dir() -> Path:
    """Where pyode keeps a libmpv it was given.

    Dropping the library here is the documented way out on Windows, where no
    package manager ships one.
    """
    return user_data_path("pyode") / "lib"


def _env_override() -> Path | None:
    raw = os.environ.get(ENV_VAR)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        return path
    found = _scan(path)
    if found is None:
        log.warning("%s is set to %s, but no libmpv is there", ENV_VAR, raw)
    return found


def _scan(directory: Path) -> Path | None:
    """The first libmpv in `directory`, preferring the newest ABI."""
    try:
        if not directory.is_dir():
            return None
        for name in LIB_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        # Some builds only ship a fully versioned name (libmpv.so.2.5.0).
        for pattern in _GLOBS:
            matches = sorted(p for p in directory.glob(pattern) if p.is_file())
            if matches:
                return matches[-1]
    except OSError:  # unreadable directory, dead network mount, ...
        log.debug("could not scan %s", directory, exc_info=True)
    return None


def _path_dirs() -> Iterator[Path]:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            yield Path(entry)


def _mpv_executable_dirs() -> Iterator[Path]:
    """Directories derived from an `mpv` binary on `PATH`.

    A package manager that ships both the player and the library puts them in
    the same tree, so the binary is the most reliable pointer we have.
    """
    binary = shutil.which("mpv")
    if not binary:
        return
    path = Path(binary)
    # Scoop installs a shim rather than a symlink; the real path is in a
    # sibling .shim file as `path = C:\...\mpv.exe`.
    shim = path.with_suffix(".shim")
    try:
        if shim.is_file():
            for line in shim.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "path":
                    path = Path(value.strip().strip('"'))
                    break
    except OSError:
        log.debug("could not read the scoop shim %s", shim, exc_info=True)

    for base in dict.fromkeys((path, path.resolve())):  # de-duplicate, keep order
        yield base.parent  # Windows: the DLL sits beside mpv.exe
        yield base.parent.parent / "lib"  # unix prefix layout
        yield base.parent.parent / "lib64"


def _windows_dirs() -> Iterator[Path]:
    env = os.environ.get

    scoop_roots = [
        Path(env("SCOOP") or (Path.home() / "scoop")),
        Path(env("SCOOP_GLOBAL") or (Path(env("ProgramData", "C:/ProgramData")) / "scoop")),
    ]
    for root in scoop_roots:
        for package in ("mpv", "mpv-git", "mpv-lite", "libmpv"):
            yield root / "apps" / package / "current"

    choco = Path(
        env("ChocolateyInstall") or (Path(env("ProgramData", "C:/ProgramData")) / "chocolatey")
    )
    for package in ("mpv.install", "mpvio.install", "mpv", "mpv-lite"):
        yield choco / "lib" / package / "tools"

    # winget unpacks into a per-package directory with a version suffix.
    local = env("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        try:
            yield from (p for p in packages.glob("*mpv*") if p.is_dir())
        except OSError:
            log.debug("could not scan %s", packages, exc_info=True)

    for program_files in ("ProgramFiles", "ProgramFiles(x86)"):
        base = env(program_files)
        if base:
            yield Path(base) / "mpv"


def _unix_dirs() -> Iterator[Path]:
    if sys.platform == "darwin":
        prefix = os.environ.get("HOMEBREW_PREFIX")
        if prefix:
            yield Path(prefix) / "lib"
        yield Path("/opt/homebrew/lib")  # Apple silicon Homebrew
        yield Path("/usr/local/lib")  # Intel Homebrew, and hand-built installs
        yield Path("/opt/local/lib")  # MacPorts
        return

    for variable in ("LD_LIBRARY_PATH",):
        for entry in os.environ.get(variable, "").split(os.pathsep):
            if entry:
                yield Path(entry)
    machine = os.uname().machine
    yield Path(f"/usr/lib/{machine}-linux-gnu")  # Debian multiarch
    yield Path("/usr/lib64")  # Fedora, openSUSE
    yield Path("/usr/lib")
    yield Path("/usr/local/lib")
    yield Path("/usr/local/lib64")
    yield Path("/lib")
    yield Path("/app/lib")  # inside a flatpak


def search_dirs() -> list[Path]:
    """Every directory we are willing to look in, in priority order."""
    sources: Iterator[Path] = iter(())
    if sys.platform == "win32":
        sources = _chain(_mpv_executable_dirs(), _windows_dirs(), _path_dirs())
    else:
        sources = _chain(_mpv_executable_dirs(), _unix_dirs())
    # A library we were handed ourselves comes last: a real installation from
    # the system package manager should always win over a stashed copy.
    return list(dict.fromkeys([*sources, bundled_dir()]))


def _chain(*iterables: Iterator[Path]) -> Iterator[Path]:
    for iterable in iterables:
        yield from iterable


def find() -> Path | None:
    """Locate libmpv, or return None."""
    override = _env_override()
    if override is not None:
        log.debug("using libmpv from %s=%s", ENV_VAR, override)
        return override
    for directory in search_dirs():
        found = _scan(directory)
        if found is not None:
            log.debug("found libmpv at %s", found)
            return found
    return None


def _loader_can_find_it() -> bool:
    """Whether python-mpv would succeed on its own."""
    if sys.platform == "win32":
        return any(ctypes.util.find_library(name) for name in LIB_NAMES[:3])
    return ctypes.util.find_library("mpv") is not None


@contextmanager
def _find_library_returning(path: Path) -> Iterator[None]:
    """Point `ctypes.util.find_library` at `path` while python-mpv imports.

    Patching the lookup is what makes this work without an environment
    variable: `LD_LIBRARY_PATH` and `DYLD_FALLBACK_LIBRARY_PATH` are read by
    the dynamic loader when the process starts, so setting them from inside
    the process is too late to have any effect.
    """
    original = ctypes.util.find_library

    def find_library(name: str) -> str | None:
        if "mpv" in name.lower():
            return str(path)
        return original(name)

    ctypes.util.find_library = find_library
    try:
        yield
    finally:
        ctypes.util.find_library = original


def load():
    """Import and return the `mpv` module, finding libmpv first if we must.

    Raises `LibmpvNotFoundError` with an actionable message when we cannot.
    """
    if "mpv" in sys.modules:
        return sys.modules["mpv"]

    if _loader_can_find_it():
        try:
            import mpv  # noqa: PLC0415 -- the whole point is to import late
        except OSError as exc:
            # The loader knows the name but the file will not load: a 32/64-bit
            # mismatch, or a missing dependency of libmpv itself.
            raise LibmpvNotFoundError(f"{exc}\n\n{install_hint()}") from exc
        return mpv

    path = find()
    if path is None:
        raise LibmpvNotFoundError(install_hint())

    if sys.platform == "win32":
        # Since Python 3.8 a DLL's own dependencies are not resolved from
        # %PATH%, so the directory has to be registered explicitly.
        try:
            os.add_dll_directory(str(path.parent))
        except OSError:
            log.debug("could not add %s to the DLL search path", path.parent, exc_info=True)

    try:
        with _find_library_returning(path):
            import mpv  # noqa: PLC0415
    except OSError as exc:
        raise LibmpvNotFoundError(
            f"Found libmpv at {path}, but it could not be loaded:\n  {exc}\n\n"
            "That usually means it was built for a different architecture than "
            f"this Python ({_architecture()}).\n\n{install_hint()}"
        ) from exc
    return mpv


def _architecture() -> str:
    import platform  # noqa: PLC0415 -- only needed on the failure path

    return f"{platform.python_version()} {platform.machine()}"


_UNIX_HINT = """\
  Arch/CachyOS:  sudo pacman -S mpv
  Debian/Ubuntu: sudo apt install libmpv2
  Fedora:        sudo dnf install mpv-libs
  openSUSE:      sudo zypper install libmpv2"""

_MACOS_HINT = """\
  brew install mpv

If mpv is already installed, check that the library is really there:
  ls $(brew --prefix)/lib/libmpv*.dylib"""

# Worth being blunt about: on Windows every package manager ships mpv.exe as a
# single statically linked binary, so installing mpv does not give you libmpv.
_WINDOWS_HINT = """\
Installing mpv with Scoop, Chocolatey or winget is not enough on Windows --
those all ship mpv.exe as one statically linked binary, with no libmpv DLL.

Download the separate libmpv build instead:
  https://github.com/zhongfly/mpv-winbuild/releases/latest
    -> mpv-dev-x86_64-<date>-git-<hash>.7z   (mpv-dev-aarch64-... on Arm)

Extract libmpv-2.dll from it and put it in:
  {bundled}

pyode looks there, so nothing else needs configuring."""


def install_hint() -> str:
    """The message shown when libmpv is missing."""
    if sys.platform == "win32":
        body = _WINDOWS_HINT.format(bundled=bundled_dir())
    elif sys.platform == "darwin":
        body = _MACOS_HINT
    else:
        body = _UNIX_HINT
    return (
        "pyode needs libmpv, which does not appear to be installed.\n\n"
        f"{body}\n\n"
        f"If libmpv is installed somewhere unusual, point pyode straight at it:\n"
        f"  {ENV_VAR}=/path/to/libmpv"
    )


def report() -> str:
    """A human-readable account of the search, for `pyode --libmpv`."""
    lines = [f"platform:  {sys.platform}", f"looking for: {', '.join(LIB_NAMES)}"]
    override = os.environ.get(ENV_VAR)
    lines.append(f"{ENV_VAR}: {override or '(not set)'}")

    loader = ctypes.util.find_library("mpv" if sys.platform != "win32" else LIB_NAMES[0])
    lines.append(f"ctypes.util.find_library: {loader or '(no result)'}")

    lines.append("\nsearched:")
    for directory in search_dirs():
        found = _scan(directory)
        if found is not None:
            lines.append(f"  {directory}  ->  {found.name}")
        elif directory.is_dir():
            lines.append(f"  {directory}  (no libmpv)")
        else:
            lines.append(f"  {directory}  (does not exist)")

    found = find()
    lines.append(f"\nresult: {found}" if found else "\nresult: not found")
    return "\n".join(lines)
