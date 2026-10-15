#!/usr/bin/env python3
"""
Hash, compile and fuzz the shunting yard implementation.

What it does
------------
1. Takes C++ source files (default: main.cpp next to this script).
2. Computes an MD5 hash of every C++ file that belongs to the project -- the
   given sources, all headers and other C++ files sitting in the same
   directories, plus anything passed with --hash-file. It reports a hash per
   file and one aggregate MD5 over all of them.
3. Compiles those sources into an executable.
4. Implements the *same* shunting yard algorithm in Python over the same token
   set ( + - * / ^ sin cos ( ) 0-9 ) -- this is the reference oracle.
5. Generates correct arithmetic expressions over those operations and runs the
   compiled executable as a subprocess, feeding it one space separated
   expression per run through stdin, then compares the printed Reverse Polish
   Notation with the oracle.
6. Executes 10 default boundary ("bound") test examples, 3 rejection tests and
   ~1000 generated expressions, then writes a pass/fail report containing the
   source hashes and the number of errors found.

Generated expressions
---------------------
Every fuzz input is a well-formed arithmetic expression: the generator builds an
expression tree, renders it as text and then *parses that text back* with the
reference grammar. The parentheses are only accepted when the reparsed tree is the
tree that was generated, so a corpus that would mean something else is impossible
by construction -- the generator aborts loudly rather than testing the wrong thing.

Parentheses can be minimal (only where precedence/associativity require them),
full (around every subexpression) or mixed per expression; see --parens. Malformed
input is deliberately *not* generated: the error paths of the target are exercised
by the fixed rejection tests, which check that a bad expression is reported on
stderr with a non-zero exit code.

Usage
-----
    python3 test.py                          # hash + compile + fuzz main.cpp
    python3 test.py main.cpp list.hpp        # explicit file set
    python3 test.py src/calc.cpp --runs 5000 # another translation unit
    python3 test.py --seed 7                 # reproducible corpus
    python3 test.py --parens min             # no redundant parentheses
    python3 test.py --no-build               # test an already built executable
    python3 test.py --binary ./main          # same, explicit executable
    python3 test.py main.cpp --compiler cl   # Visual Studio (cl.exe) toolchain

Windows
-------
The script runs unchanged on Windows (cmd.exe or PowerShell):

    g++ -std=c++11 -g main.cpp -o main.exe
    python test.py main.cpp

Visual Studio (MSVC) toolchain
------------------------------
Microsoft cl.exe is a first-class target next to g++/clang++. Three situations
are handled automatically:

  * cl.exe already on PATH, i.e. the shell was started through a Visual Studio
    "Developer Command Prompt" / "x64 Native Tools" prompt:

        cl /nologo /EHsc /Zi main.cpp /Fe:main.exe
        python test.py main.cpp --compiler cl

  * plain cmd.exe / PowerShell, where cl.exe is *not* on PATH: the script looks
    for the Visual Studio installation (vswhere.exe, then the usual install
    directories), runs vcvarsall.bat / vcvars64.bat in a helper cmd.exe and uses
    the resulting environment (PATH, INCLUDE, LIB, ...). Nothing has to be set
    up by hand:

        python test.py main.cpp --compiler cl
        python test.py main.cpp --arch x86          # 32-bit build
        python test.py main.cpp --vs-install "C:\\Program Files\\Microsoft Visual Studio\\18\\Community"

  * clang-cl.exe, which takes MSVC-style options, is recognised as well
    (--compiler clang-cl).

An explicit compiler is never guessed: --compiler cl / clang-cl selects MSVC-style
compilation, any other name (g++, clang++, c++, ...) selects the GCC-style command
line. With a bare "cl" the environment is resolved through vcvars; with a full path
to cl.exe the script still tries to find the Visual Studio environment next to it,
so the INCLUDE/LIB variables are available.

Because MSVC spells the options differently, --cxxflags, --debug-flag and --std are
translated for cl.exe (`-std=c++11 -g` becomes `/std:c++14 /Zi`, `-O2` becomes `/O2`,
`-Wall` becomes `/W3` and is merged with `-Wextra` into a single `/W4`, ...). The
translation, including every option that is deliberately dropped, is printed before
the build and recorded in the report, so the actual command line is never a surprise.
Options that cannot be translated (an unknown -f flag, -pthread) and options that
would fight with the ones test.py adds (-o, -c, /Fe, /Fo) are a hard error instead of
a silently different build. --ldflags is passed to GCC-style compilers only, because
cl.exe compiles and links in one step and does not accept them.

For cl.exe the object file and the debug database are written next to the executable
(/Fo), and the executable is emitted at the requested --output path (/Fe).

Platform differences are handled automatically:
  * the executable is named "main" on POSIX and "main.exe" on Windows, and both
    --output/--binary accept the name with or without the ".exe" suffix;
  * the C++ runtime emits CRLF line endings on Windows, so captured output is
    normalised to LF before parsing;
  * the POSIX executable-bit check is skipped on Windows, where it is meaningless;
  * the compiler is found on PATH (cl, clang-cl, g++, clang++, c++) or through the
    Visual Studio environment; override it with --compiler;
  * the target is launched directly (no shell), so paths with spaces or non-ASCII
    characters work, and its output is decoded as UTF-8 regardless of the console
    code page.

Exit status: 0 = all checks passed, 1 = at least one error, 2 = setup problem
(missing source, no compiler, compilation failure).
Only the Python standard library is used.
"""

import argparse
import datetime
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Platform handling
# ---------------------------------------------------------------------------

IS_WINDOWS = os.name == "nt"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Toolchain families. "msvc" is cl.exe, "clang-cl" is the MSVC-compatible driver
# of LLVM; both speak the /option command line. Everything else is treated as a
# GCC-style compiler (g++, clang++, c++, MinGW, MSYS, ...).
KIND_MSVC = "msvc"
KIND_CLANG_CL = "clang-cl"
KIND_GCC = "gcc"
MSVC_KINDS = (KIND_MSVC, KIND_CLANG_CL)


def executable_name(base):
    """Executable file name for this platform (g++/MinGW append ".exe" on Windows)."""
    return base + ".exe" if IS_WINDOWS else base


def default_source_path():
    """Default translation unit: main.cpp next to this script."""
    return os.path.join(SCRIPT_DIR, "main.cpp")


def default_output_path(source):
    """Build next to the source file, exactly like the usual compiler invocation."""
    return os.path.join(os.path.dirname(os.path.abspath(source)), executable_name("main"))


def resolve_existing_executable(requested):
    """
    Return the absolute path of an existing executable, or None.

    Tolerates the ".exe" suffix being present or absent on Windows, so both
    "--binary main" and "--binary main.exe" work there.
    """
    candidates = [requested]
    if IS_WINDOWS:
        if requested.lower().endswith(".exe"):
            candidates.append(requested[: -len(".exe")])
        else:
            candidates.append(requested + ".exe")

    for candidate in candidates:
        path = os.path.abspath(candidate)
        if os.path.isfile(path):
            return path
    return None


def locate_built_executable(output):
    """Find the executable produced by the compiler (handles the implicit .exe)."""
    return resolve_existing_executable(output)


def normalize_newlines(text):
    """
    Convert CRLF / CR line endings to LF.

    The target writes "\\r\\n" on Windows because its stdout is a CRT text-mode
    stream; parsing must not depend on the platform.
    """
    if text is None:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def format_command(parts):
    """Render a command line for humans (quote only what needs quoting)."""
    return " ".join('"%s"' % part if " " in part else part for part in parts)


# ---------------------------------------------------------------------------
# Hashing of the C++ sources
# ---------------------------------------------------------------------------

CPP_EXTENSIONS = (".cpp", ".cc", ".cxx", ".c++", ".cp")
HEADER_EXTENSIONS = (".h", ".hpp", ".hh", ".hxx", ".h++", ".inl", ".ipp")

# Everything that counts as a "C++ file" for hashing purposes.
HASH_EXTENSIONS = CPP_EXTENSIONS + HEADER_EXTENSIONS


def relative_path(path, base_dir):
    """Path relative to base_dir (forward slashes), so hashes are machine independent."""
    try:
        relative = os.path.relpath(os.path.abspath(path), base_dir)
    except ValueError:  # different drive on Windows
        relative = os.path.abspath(path)
    return relative.replace("\\", "/")


def collect_cpp_files(sources, extra_files):
    """
    All C++ files belonging to the project:

      * every file passed on the command line (sources and headers),
      * every .cpp/.h/.hpp/... file found in the directories of those files,
      * everything listed with --hash-file.

    AppleDouble metadata files ("._main.cpp", written when a macOS or SMB volume
    is involved) are not C++ sources and are skipped.

    The result is unique and sorted, so the aggregate hash is deterministic.
    """
    found = {}

    def add(path):
        absolute = os.path.abspath(path)
        if os.path.isfile(absolute):
            found[absolute] = True

    def is_project_file(entry):
        # "._main.cpp" is an AppleDouble metadata file, not a translation unit.
        if entry.startswith("._"):
            return False
        return entry.lower().endswith(HASH_EXTENSIONS)

    for source in sources:
        add(source)
        directory = os.path.dirname(os.path.abspath(source))
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            entries = []
        for entry in entries:
            if is_project_file(entry):
                add(os.path.join(directory, entry))

    for extra in extra_files:
        if not os.path.isfile(extra):
            raise FileNotFoundError(extra)
        add(extra)

    return sorted(found)


def md5_of_file(path, chunk_size=65536):
    """MD5 and size of a file, read in binary chunks."""
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), os.path.getsize(path)


def hash_files(paths, base_dir):
    """[(relative path, md5, size in bytes)] for every path, sorted by path."""
    entries = []
    for path in paths:
        file_md5, size = md5_of_file(path)
        entries.append((relative_path(path, base_dir), file_md5, size))
    return sorted(entries)


def aggregate_md5(entries):
    """
    One MD5 over all C++ files.

    Built from the sorted "<path>:<file md5>" lines, so it changes when any file
    changes, when files are added or removed, and when a file is renamed -- and it
    does not depend on the machine the project lives on.
    """
    digest = hashlib.md5()
    for relative, file_md5, _size in sorted(entries):
        digest.update(("%s:%s\n" % (relative, file_md5)).encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def find_compiler(requested=None):
    """
    Locate the compiler to use.

    Without --compiler the order is cl (MSVC), clang-cl, g++, clang++, c++; a
    compiler that is only present in the CL environment variable (set by hand or
    by a build shell that does not extend PATH) is honoured as well.

    Returns (path or None, kind); the kind decides how the command line is built.
    """
    if requested:
        return resolve_compiler(requested)

    for candidate in ("cl", "clang-cl", "g++", "clang++", "c++"):
        found = shutil.which(candidate)
        if found:
            return found, compiler_kind(found)

    # cl.exe is not on PATH. If Visual Studio is installed but vcvars was never
    # run, find_visual_studio() plus the vcvars environment makes cl usable.
    if IS_WINDOWS and find_visual_studio():
        return None, KIND_MSVC
    return None, None


def compiler_kind(compiler):
    """Toolchain family of a compiler name or path."""
    name = os.path.basename(str(compiler)).lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    if name in ("clang-cl", "clang_cl"):
        return KIND_CLANG_CL
    if name == "cl":
        return KIND_MSVC
    return KIND_GCC


def resolve_compiler(requested):
    """
    Turn --compiler into an absolute executable path plus its kind.

    Accepts an absolute/relative path, a bare name resolved through PATH, and the
    names "cl" / "clang-cl" even when they are not on PATH but Visual Studio is
    installed (the vcvars environment then provides them at build time).
    """
    kind = compiler_kind(requested)

    if os.path.isfile(requested):
        return os.path.abspath(requested), kind

    found = shutil.which(requested)
    if found:
        return found, kind

    if kind in MSVC_KINDS and IS_WINDOWS and find_visual_studio():
        # The path is added by the vcvars environment before the build starts.
        return requested, kind
    return None, kind


# ---------------------------------------------------------------------------
# Visual Studio environment (for cl.exe / clang-cl.exe)
# ---------------------------------------------------------------------------

VSWHERE_RELATIVE = os.path.join("Microsoft Visual Studio", "Installer", "vswhere.exe")

VS_ARCHES = {
    "x64": ("x64", "amd64"),
    "amd64": ("x64", "amd64"),
    "x86": ("x86", "x86"),
    "win32": ("x86", "x86"),
    "arm64": ("arm64", "arm64"),
    "arm": ("arm", "arm"),
}


def find_vswhere():
    """Locate vswhere.exe, the supported way to find Visual Studio installs."""
    found = shutil.which("vswhere")
    if found:
        return found
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    for root in roots:
        if not root:
            continue
        candidate = os.path.join(root, VSWHERE_RELATIVE)
        if os.path.isfile(candidate):
            return candidate
    return None


def find_visual_studio():
    """
    Directory of the Visual Studio installation that owns a C++ toolchain.

    Tries vswhere first (any edition, any version, also Build Tools), then the
    well-known install directories. Returns None when nothing is found.
    """
    if not IS_WINDOWS:
        return None

    vswhere = find_vswhere()
    if vswhere:
        command = [
            vswhere,
            "-latest",
            "-products", "*",
            "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property", "installationPath",
        ]
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0:
            for line in normalize_newlines(proc.stdout).splitlines():
                if line.strip() and os.path.isdir(line.strip()):
                    return line.strip()

    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in program_files:
        if not root:
            continue
        base = os.path.join(root, "Microsoft Visual Studio")
        if not os.path.isdir(base):
            continue
        # Newest version/year first: "2022", "18", ...
        for version in sorted(os.listdir(base), reverse=True):
            version_dir = os.path.join(base, version)
            if not os.path.isdir(version_dir):
                continue
            for edition in sorted(os.listdir(version_dir)):
                if os.path.isfile(os.path.join(version_dir, edition, "VC", "Auxiliary", "Build", "vcvarsall.bat")):
                    return os.path.join(version_dir, edition)
    return None


def vcvars_scripts(install_dir, arch):
    """
    vcvars batch files to try, best match first.

    vcvarsall.bat with an explicit architecture is preferred over the
    architecture-specific files (vcvars64.bat, vcvars32.bat, ...).
    """
    build_dir = os.path.join(install_dir, "VC", "Auxiliary", "Build")
    names, arg = VS_ARCHES.get((arch or "x64").lower(), VS_ARCHES["x64"])

    candidates = []
    vcvarsall = os.path.join(build_dir, "vcvarsall.bat")
    if os.path.isfile(vcvarsall):
        candidates.append((vcvarsall, [arg]))
    for name in names:
        specific = os.path.join(build_dir, "vcvars%s.bat" % name)
        if os.path.isfile(specific):
            candidates.append((specific, []))

    for candidate, candidate_arg in candidates:
        yield candidate, candidate_arg


def parse_set_output(text):
    """
    Parse "NAME=value" lines printed by the batch `set` command.

    Windows environment variable names are case-insensitive and cmd.exe prints
    them exactly as they were set, so "PATH" and "Path" can appear as separate
    lines. Names are upper-cased to merge them (a lower-case name would shadow
    nothing on Windows because the process environment is case-insensitive).
    """
    environment = {}
    for line in normalize_newlines(text).splitlines():
        if "=" not in line or line.startswith("="):
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        environment[name.upper() if IS_WINDOWS else name] = value
    return environment


def visual_studio_environment(script, script_args, timeout=120):
    """
    Run a vcvars batch file and return the environment it creates.

    cmd.exe is required because the script is a batch file. The environment is
    captured through `set` on stdout instead of parsing the script, which keeps
    working when Microsoft changes the layout of the batch files. PATH is
    preserved when the captured value looks truncated.

    Returns (command, environment or None, info lines, error message or None).
    """
    if not IS_WINDOWS:
        return None, None, [], "the Visual Studio environment is only available on Windows"

    lines = [
        "@echo off",
        'call "%s"%s >nul 2>&1' % (script, "".join(" " + a for a in script_args)),
        "if errorlevel 1 exit /b 1",
    ]
    for name in ("VSCMD_VER", "VSCMD_ARG_TGT_ARCH", "VisualStudioVersion", "VCToolsVersion"):
        lines.append('if defined %s echo %s=%%%s%%' % (name, name, name))
    lines.append("set")
    payload = "\r\n".join(lines) + "\r\n"

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".bat", prefix="dsh-vcvars-", delete=False, encoding="utf-8", newline=""
    )
    try:
        handle.write(payload)
        handle.close()
        command = ["cmd.exe", "/d", "/c", handle.name]
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return command, None, [], "loading the Visual Studio environment timed out after %gs" % timeout
        except OSError as exc:
            return command, None, [], "cannot run %s: %s" % (script, exc)
    finally:
        try:
            os.remove(handle.name)
        except OSError:
            pass

    info = ["%s%s" % (script, "".join(" " + a for a in script_args))]
    environment = parse_set_output(proc.stdout)
    for name in ("VSCMD_VER", "VSCMD_ARG_TGT_ARCH", "VisualStudioVersion", "VCToolsVersion"):
        if name in environment:
            info.append("%s=%s" % (name, environment[name]))
            del environment[name]

    if proc.returncode != 0:
        return command, None, info, "%s failed with exit code %d" % (script, proc.returncode)
    if not environment.get("INCLUDE") or not environment.get("LIB"):
        return command, None, info, (
            "%s did not set INCLUDE/LIB; is the C++ workload of Visual Studio installed?"
            " Pass --vs-install <dir> or run the build from a Developer Command Prompt"
            % script
        )

    # Long PATH values can come back truncated from cmd.exe; keeping the current
    # PATH in front guarantees that winnt/system32 and the tool directories stay
    # reachable, while the captured CL/LINK/INCLUDE/LIB win.
    current = os.environ.get("PATH", "")
    captured = environment.get("PATH", "")
    if current:
        environment["PATH"] = captured + os.pathsep + current

    return command, environment, info, None


# ---------------------------------------------------------------------------
# Compiler options: GCC-style <-> MSVC-style
# ---------------------------------------------------------------------------

DEFAULT_CXXFLAGS = "-std=c++11 -g"
DEFAULT_DEBUG_FLAG = "-g"
MSVC_DEFAULT_DEBUG_FLAG = "/Zi"

# Optimization levels are lowercase "o" flags, not "-o <file>"; they must survive
# the check for an output-file option.
GCC_OPTIMIZATION_FLAGS = (
    "-o0", "-o1", "-o2", "-o3", "-os", "-ofast", "-og", "-oz", "-od", "-ox",
)

# Options that are also GCC spellings for a *separate* argument: "-isystem=dir" is
# not a thing, so only a real value counts as attached.
SEPARATE_VALUE_FLAGS = ("-include", "-isystem")

# MSVC warning levels, weakest first. cl.exe accepts only one, so the strongest
# requested level wins and the others are dropped instead of triggering D9025.
WARNING_LEVELS = {"/w": 0, "/w0": 1, "/w1": 2, "/w2": 3, "/w3": 4, "/w4": 5}

# GCC-style spelling -> MSVC spelling for options that take no value.
MSVC_SIMPLE_OPTIONS = {
    "-g": None, "/g": None,                       # debug info, handled separately
    "-o0": "/Od", "-o1": "/O1", "-o2": "/O2", "-o3": "/O2", "-os": "/O2", "-ofast": "/O2",
    "-od": "/Od", "-ox": "/Ox", "-og": "/Od", "-oz": "/O2",
    "-wall": "/W3", "-wextra": "/W4", "-w": "/w", "-werror": "/WX",
    "-pedantic": None, "-pedantic-errors": None, "-ansi": None,
    "-fno-omit-frame-pointer": None, "-fomit-frame-pointer": None,
    "-ffast-math": "/fp:fast", "-fno-exceptions": None, "-fexceptions": "/EHsc",
    "-fno-rtti": "/GR-", "-frtti": "/GR", "-fno-strict-aliasing": None,
    "-fstrict-aliasing": None, "-finline-functions": None, "-funroll-loops": None,
    "-fsanitize=address": None, "-fsanitize=undefined": None,
    "-m64": None, "-m32": None, "-w64": None,
    "-xc++": None, "-xc": None,
    "--std=c++11": "/std:c++14", "--std=c++14": "/std:c++14",
    "--std=c++17": "/std:c++17", "--std=c++20": "/std:c++20",
}

# Options whose value is a separate argument or attached with "=" or ":".
MSVC_VALUE_OPTIONS = {
    "-i": "/I", "-d": "/D", "-u": "/U", "-l": "/DEFAULTLIB:",
    "-include": "/FI:", "-isystem": "/external:I",
    "--std": "/std:", "/std": "/std:",
}

# GCC spellings only accepted with "=" (the bare option usually means something
# else to MSVC, so translating "-f" would be guesswork).
MSVC_EQUALS_OPTIONS = {
    "-std": "/std:", "--std": "/std:", "-x": None,
    "-imacros": None, "-fvisibility": None, "-march": None, "-mtune": None,
}

# Standard for each C++ mode, used when -std=c++NN has no MSVC equivalent.
CPP_STANDARD_NAMES = {
    "98": "C++98", "03": "C++03", "0x": "C++11", "11": "C++11", "1y": "C++14",
    "14": "C++14", "1z": "C++17", "17": "C++17", "2a": "C++20", "20": "C++20",
    "2b": "C++23", "23": "C++23", "2c": "C++26", "26": "C++26",
}


def normalize_standard(value):
    """Normalize a -std= value ("c++11", "gnu++11", "c11") to lowercase."""
    return value.strip().lower()


def msvc_standard_flag(value):
    """MSVC /std: flag for a -std= value, or None when there is no equivalent."""
    standard = normalize_standard(value)
    if standard.startswith("gnu"):
        standard = "c" + standard[len("gnu"):]
    key = standard[3:] if standard.startswith("c++") else standard
    if key in CPP_STANDARD_NAMES:
        name = CPP_STANDARD_NAMES[key]
        if name in ("C++98", "C++03"):
            return None                      # MSVC cannot select these; the default is used
        if name in ("C++23", "C++26"):
            return "/std:c++latest"          # newest mode available in the installed toolset
        return "/std:" + name.lower()
    if standard.startswith("c++"):
        return "/std:" + standard            # forward the unknown mode and let cl report it
    return None                              # C standard: leave the default alone


def msvc_debug_mode(debug_value):
    """MSVC debug flag for a debug value, or None when it is not a debug mode."""
    if debug_value is None:
        return None
    debug_value = debug_value.strip().lower()
    if debug_value in ("", "g", "3", "ggdb", "ggdb3", "gdwarf", "gdwarf-2", "gdwarf-3", "gdwarf-4", "gdwarf-5"):
        return "/Zi"
    return None


def msvc_options(flags, debug_flag):
    """
    Translate GCC-style compiler flags to cl.exe options.

    Returns (options, notes, warnings). A warning means that the flag was dropped
    or replaced by a deliberate default; a hard error (Exception) is raised only
    when translating would silently change what is compiled.
    """
    debug_flag = (debug_flag or "").strip()
    options = []
    notes = []
    warnings = []
    want_debug = False
    index = 0

    def take_value(flag):
        nonlocal index
        if len(flag) > 2 and flag[2] in "=:":
            return flag[3:], False
        index += 1
        if index >= len(flags):
            raise ValueError("%s expects a value" % flag)
        return flags[index], True

    while index < len(flags):
        raw = flags[index]
        flag = raw.strip()
        if not flag:
            index += 1
            continue
        low = flag.lower()

        # A value attached to the option instead of given as the next argument:
        # -Iinc, -DX=1, -lstdc++, -isystem=C:/inc. The longest spelling is tried
        # first, so -isystem is not mistaken for -i, and "=" / ":" separators are
        # accepted as well (-l=m, -isystem=C:/inc).
        attached_key = None
        attached_value = None
        # Bare -include / -isystem take the next argument; only their
        # "=" / ":" forms carry an attached value.
        span = () if low in SEPARATE_VALUE_FLAGS else ("-i", "-d", "-u", "-l")
        for candidate in ("-include", "-isystem") + span:
            if not low.startswith(candidate) or len(flag) <= len(candidate):
                continue
            remainder = flag[len(candidate):]
            if remainder[:1] in ("=", ":"):
                if len(remainder) > 1:
                    attached_key, attached_value = candidate, remainder[1:]
            elif candidate not in SEPARATE_VALUE_FLAGS:
                attached_key, attached_value = candidate, remainder
            break

        # The output-file option is a lower-case "o" (-o out, -oout); "-O2" is an
        # optimization level and must not be mistaken for it.
        output_option = flag[:2] == "-o" or low in ("-c", "/c", "/o") \
            or low.startswith("/o") or low.startswith("/fe") or low.startswith("/fo") \
            or low.startswith("/fx") \
            or (low.startswith("/c") and not low.startswith("/c++"))
        if output_option and low not in GCC_OPTIMIZATION_FLAGS:
            if low.startswith("/fo") or low.startswith("/fx"):
                raise ValueError(
                    "/Fo is managed by test.py: name the executable with --output and let the "
                    "object file stay next to it"
                )
            if low.startswith("/fe"):
                raise ValueError("/Fe is managed by test.py: name the executable with --output")
            if low in ("/c", "-c"):
                raise ValueError(
                    "%s is not supported: the target has to be linked into an executable" % flag
                )
            else:
                raise ValueError(
                    "%s is not supported: use the --output option of test.py to name the executable"
                    % flag
                )
        elif attached_key is not None and attached_key in ("-i", "-d", "-u", "-l", "-include", "-isystem"):
            prefix = MSVC_VALUE_OPTIONS[attached_key]
            if prefix is None:
                warnings.append("dropped %s (no MSVC equivalent)" % flag)
            else:
                options.append(prefix + attached_value)
        elif low.startswith("-g"):
            # GCC spelling: the value always follows the "-g" itself
            # (-g, -g3, -ggdb, -gdwarf-4), optionally after "=" or ":".
            value = flag[2:]
            if value[:1] in ("=", ":"):
                value = value[1:]
            mode = msvc_debug_mode(value)
            if mode is None:
                warnings.append("dropped %s (no MSVC equivalent)" % flag)
            else:
                want_debug = True
        elif low in MSVC_SIMPLE_OPTIONS:
            if low in ("-g", "/g"):
                want_debug = True
            elif low == "-m64":
                pass
            elif low == "-m32":
                warnings.append("dropped -m32 (use test.py --arch x86 to build 32-bit)")
            else:
                mapped = MSVC_SIMPLE_OPTIONS[low]
                if mapped is None:
                    warnings.append("dropped %s (no MSVC equivalent)" % flag)
                else:
                    options.append(mapped)
        elif low.startswith("-std=") or low.startswith("--std=") or low.startswith("/std:"):
            value = flag.split("=", 1)[1] if "=" in flag else flag.split(":", 1)[1]
            standard = normalize_standard(value)
            # "c++11" means *at least* C++11 for MSVC, which starts at C++14.
            if standard in ("c++11", "c++0x", "gnu++11", "gnu++0x"):
                options.append("/std:c++14")
                notes.append("%s -> /std:c++14 (MSVC has no C++11 mode; C++14 is the closest)" % flag)
                index += 1
                continue
            mapped = msvc_standard_flag(value)
            if mapped is None:
                warnings.append("dropped %s (no MSVC equivalent)" % flag)
            else:
                options.append(mapped)
        elif low in MSVC_VALUE_OPTIONS:
            value, attached = take_value(flag)
            prefix = MSVC_VALUE_OPTIONS[low]
            if prefix is None:
                warnings.append("dropped %s%s (no MSVC equivalent)"
                                % (flag, "=" + value if attached else " " + value))
            else:
                options.append(prefix + value)
        elif low in MSVC_EQUALS_OPTIONS or ("=" in flag and flag.split("=", 1)[0].lower() in MSVC_EQUALS_OPTIONS):
            name, value = flag.split("=", 1)
            prefix = MSVC_EQUALS_OPTIONS[name.lower()]
            if prefix is None:
                warnings.append("dropped %s (no MSVC equivalent)" % flag)
            else:
                options.append(prefix + value)
        elif low.startswith(("-f", "-m")):
            # cl.exe has no /f* or /m* options; dropping silently could quietly
            # change the build, so only the documented spellings are accepted.
            if low.startswith("-fdiagnostics-color") or low.startswith("-fcolor-diagnostics") \
                    or low.startswith("-fno-diagnostics-color") or low.startswith("-fmessage-length"):
                warnings.append("dropped %s (diagnostic formatting only)" % flag)
            else:
                raise ValueError(
                    "cannot translate %s for cl.exe; pass the matching /option in --cxxflags"
                    % flag
                )
        elif low[:2] == "-o":
            raise ValueError(
                "cannot translate the optimization level %s for cl.exe "
                "(use -Od, -O1, -O2 or -Ox)" % flag
            )
        elif low.startswith(("-p", "-w", "-flto")) or low in ("-pipe", "-s", "-static", "-shared"):
            if low == "-pthread":
                raise ValueError(
                    "%s is not supported by cl.exe (link with the MSVC runtime instead)" % flag
                )
            warnings.append("dropped %s (no MSVC equivalent)" % flag)
        elif flag.startswith("/"):
            options.append(flag)             # already an MSVC option
        else:
            raise ValueError(
                "cannot translate %s for cl.exe; pass MSVC options in --cxxflags" % flag
            )
        index += 1

    # -Wall -Wextra would become "/W3 /W4" and cl.exe answers with
    # "D9025: overriding '/W3' with '/W4'"; keep only the strongest level.
    levels = [
        (position, option) for position, option in enumerate(options)
        if option.lower() in WARNING_LEVELS
    ]
    if len(levels) > 1:
        kept_position, kept_option = max(levels, key=lambda item: WARNING_LEVELS[item[1].lower()])
        dropped = [option for position, option in levels if position != kept_position]
        level_positions = {position for position, _ in levels}
        options = [
            option for position, option in enumerate(options)
            if position not in level_positions or position == kept_position
        ]
        notes.append("kept %s and dropped %s (cl.exe accepts a single warning level)"
                     % (kept_option, ", ".join(dropped)))

    if want_debug:
        # Only bare "-g" pieces mean "just enable debug info"; any other piece in
        # --debug-flag is a deliberate choice and is translated on its own.
        bare = ("-g", "g", "/g", "")
        pieces = [piece for piece in debug_flag.split() if piece.lower() not in bare]
        if pieces:
            for piece in pieces:
                if piece.startswith("/"):
                    options.append(piece)    # an explicit MSVC debug option is used as-is
                else:
                    mapped = msvc_debug_mode(piece.lstrip("-/"))
                    if mapped:
                        options.append(mapped)
                        notes.append("%s -> %s" % (piece, mapped))
                    else:
                        warnings.append("dropped the debug flag %s (no MSVC equivalent)" % piece)
        else:
            options.append(MSVC_DEFAULT_DEBUG_FLAG)
            notes.append("%s -> %s" % (debug_flag or DEFAULT_DEBUG_FLAG, MSVC_DEFAULT_DEBUG_FLAG))

    return options, notes, warnings


def gcc_options(flags):
    """Pass GCC-style flags through untouched (only a sanity check)."""
    for flag in flags:
        lowered = flag.lower()
        if lowered == "-o" or (lowered.startswith("-o") and lowered not in ("-o0", "-o1", "-o2", "-o3", "-os", "-ofast")):
            raise ValueError(
                "%s is not supported: use --output/-o of test.py to name the executable" % flag
            )
        if flag == "-c":
            raise ValueError("-c is not supported: the target must be linked into an executable")
    return list(flags), [], []


def resolve_in_environment(executable, environment):
    """
    Absolute path of a compiler, searched in the build environment's PATH.

    Required on Windows: CreateProcess resolves a bare program name against the
    PATH of the *calling* process, not against the environment block that is
    passed in, so "cl" would never be found when it comes from vcvars alone.
    """
    if os.path.isabs(executable) and os.path.isfile(executable):
        return executable
    if environment:
        search_path = None
        for name, value in environment.items():
            if name.upper() == "PATH":
                search_path = value
                break
        if search_path:
            found = shutil.which(executable, path=search_path)
            if found:
                return found
    return shutil.which(executable) or executable


def build_executable(compiler, flags, sources, output, timeout=180, environment=None):
    """
    Compile and link sources into output (GCC-style command line).

    Returns (command, returncode or None on failure to run, stdout, stderr).
    """
    compiler = resolve_in_environment(compiler, environment)
    command = [compiler] + flags + [os.path.abspath(s) for s in sources] + ["-o", output]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return command, None, "", "compilation timed out after %gs" % timeout
    except OSError as exc:
        return command, None, "", str(exc)

    return (
        command,
        proc.returncode,
        normalize_newlines(proc.stdout),
        normalize_newlines(proc.stderr),
    )


def build_msvc_executable(compiler, options, sources, output, object_file, environment, timeout=180):
    """
    Compile and link with cl.exe / clang-cl.exe.

    cl.exe links automatically when no /c is given, so one invocation produces the
    executable. /Fe names it (the exact path, extension included, to avoid cl's
    ".exe" guessing), /Fo keeps the object file out of the project directory.

    /Fo accepts one object file name only for a single translation unit; with
    several sources cl.exe insists on a directory (trailing separator) and
    otherwise fails with "D8036: not allowed with multiple source files".

    Returns (command, returncode or None on failure to run, stdout, stderr).
    """
    compiler = resolve_in_environment(compiler, environment)
    command = [compiler, "/nologo"] + list(options)
    command += [os.path.abspath(source) for source in sources]
    if len(sources) > 1:
        object_directory = os.path.dirname(object_file) or os.curdir
        command += ["/Fe:" + output, "/Fo:" + object_directory + os.sep]
    else:
        command += ["/Fe:" + output, "/Fo:" + object_file]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return command, None, "", "compilation timed out after %gs" % timeout
    except OSError as exc:
        return command, None, "", str(exc)

    return (
        command,
        proc.returncode,
        normalize_newlines(proc.stdout),
        normalize_newlines(proc.stderr),
    )


# ---------------------------------------------------------------------------
# Reference oracle
# ---------------------------------------------------------------------------
# The readable reference implementation of the shunting yard algorithm and the
# generator that produced the obfuscated function below live in shunt.py.
# Regenerate the marked block with:    python3 shunt.py --write
# Prove it still matches shunt.py with: python3 shunt.py --verify

# Token alphabet of the assignment; also used to build fuzz input.
OPERATORS = ("+", "-", "*", "/", "^")
FUNCTIONS = ("sin", "cos")
SUPPORTED_TOKENS = set(OPERATORS) | set(FUNCTIONS) | {"(", ")"}
SEPARATOR = " "

# How generated expressions are parenthesised: only where the grammar requires it,
# everywhere, or either one per expression. "mixed" is the default because it puts
# both the precedence logic and the explicit grouping of the target under test.
PARENTHESIS_MODES = ("mixed", "min", "full")

# --- BEGIN GENERATED (shunt.py) ---
def expected_for(_39):
    _0a={"\x2a":2,"\x2b":1,"\x2d":1,"\x2f":2,"\x5e":3,"\x63\x6f\x73":4,"\x73\x69\x6e":4}
    _da={"\x73\x69\x6e","\x63\x6f\x73"}
    _c9={"\x5e"}
    _7e="\x30\x31\x32\x33\x34\x35\x36\x37\x38\x39"
    _oz="\x20"
    _dk="\x28"
    _o9="\x29"
    _9o="\x6f\x6b"
    _e8="\x65\x72\x72\x6f\x72"
    _u1=[_vr for _vr in _39.split(_oz) if _vr]
    _h8=[];_if=[]
    for _vr in _u1:
        if _vr and all(_2e in _7e for _2e in _vr):_h8.append(_vr)
        elif _vr in _da:_if.append(_vr)
        elif _vr in _0a and _vr not in _da:
            while _if and _if[-1]!=_dk and (_0a.get(_if[-1],0)>_0a.get(_vr,0) or (_0a.get(_if[-1],0)==_0a.get(_vr,0) and _vr not in _c9)):_h8.append(_if.pop())
            _if.append(_vr)
        elif _vr==_dk:_if.append(_vr)
        elif _vr==_o9:
            _sg=0
            while _if:
                _fz=_if.pop()
                if _fz==_dk:_sg=1;break
                _h8.append(_fz)
            if not _sg:return (_e8,"\x6d\x69\x73\x6d\x61\x74\x63\x68\x65\x64\x20\x70\x61\x72\x65\x6e\x74\x68\x65\x73\x65\x73")
            if _if and _if[-1] in _da:_h8.append(_if.pop())
        else:return (_e8,"\x75\x6e\x6b\x6e\x6f\x77\x6e\x20\x74\x6f\x6b\x65\x6e\x3a\x20"+_vr)
    while _if:
        _fz=_if.pop()
        if _fz==_dk:return (_e8,"\x6d\x69\x73\x6d\x61\x74\x63\x68\x65\x64\x20\x70\x61\x72\x65\x6e\x74\x68\x65\x73\x65\x73")
        _h8.append(_fz)
    return (_9o,_h8)
# --- END GENERATED (shunt.py) ---


def tokenize(expression):
    """Split an expression the way main.cpp does; used to build fuzz input."""
    return [token for token in expression.split(SEPARATOR) if token != ""]


def is_number(token):
    """Digit test mirroring isNumber() in main.cpp; used to build fuzz input."""
    if token == "":
        return False
    for char in token:
        if char not in "0123456789":
            return False
    return True


# ---------------------------------------------------------------------------
# Target program interaction
# ---------------------------------------------------------------------------

TARGET_HEADER = "\n\n"


def parse_target_output(stdout):
    """
    Extract the RPN token list from the target's stdout.

    main.cpp prints: <echo of input> + blank line + "t1, t2, ..., " (note the
    trailing separator coming from List::print). Returns None if the output does
    not follow that shape.

    The input is normalised to LF first, so the same parse works for the CRLF
    output produced by the Windows C++ runtime.
    """
    stdout = normalize_newlines(stdout)
    marker = stdout.find(TARGET_HEADER)
    if marker == -1:
        return None
    tail = stdout[marker + len(TARGET_HEADER):].strip()
    if tail == "":
        return []
    return [token.strip() for token in tail.split(",") if token.strip() != ""]


def run_target(binary, expression, timeout):
    """Run one expression; returns dict(status, returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [binary],
            input=expression + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "returncode": None, "stdout": "", "stderr": ""}
    except OSError as exc:
        raise RuntimeError("cannot execute %s: %s" % (binary, exc))

    return {
        "status": "ran",
        "returncode": proc.returncode,
        "stdout": normalize_newlines(proc.stdout),
        "stderr": normalize_newlines(proc.stderr),
    }


def check_case(binary, expression, timeout):
    """
    Compare the target against the oracle for one expression.

    Returns (category, expected, actual). category is None when the case passed.
    """
    kind, expected = expected_for(expression)
    result = run_target(binary, expression, timeout)

    if result["status"] == "timeout":
        return ("timeout", expected, "no answer within %gs" % timeout)

    returncode = result["returncode"]
    stdout = result["stdout"]
    stderr = result["stderr"].strip()

    if returncode < 0:
        return ("crash", expected, "killed by signal %d" % -returncode)

    if kind == "error":
        # The oracle rejects this expression: the target has to reject it too.
        if returncode == 0:
            return (
                "not_rejected",
                expected,
                "exit=0, stdout=%r (expected rejection: %s)" % (stdout.strip(), expected),
            )
        if stderr == "":
            return ("silent_rejection", expected, "exit=%d but stderr is empty" % returncode)
        return (None, expected, "exit=%d, stderr=%r" % (returncode, stderr))

    # Oracle accepts the expression: the target must print identical RPN and exit 0.
    if returncode != 0:
        return (
            "nonzero_exit",
            expected,
            "exit=%d, stderr=%r" % (returncode, stderr or stdout.strip()),
        )

    actual = parse_target_output(stdout)
    if actual is None:
        return ("bad_format", expected, "unparsable stdout=%r" % stdout)

    if actual != expected:
        return ("mismatch", expected, actual)

    if stderr != "":
        return ("unexpected_stderr", expected, "stderr=%r" % stderr)

    return (None, expected, actual)


# ---------------------------------------------------------------------------
# Test corpora
# ---------------------------------------------------------------------------

# 10 default boundary ("bound") test examples.
DEFAULT_BOUND_TESTS = [
    "5",                                          # minimal expression
    "0 + 0",                                      # zero operands
    "123 + 456 * 789",                            # multi-digit numbers
    "1 + 3 - 4",                                  # left-associative chain
    "2 ^ 3 ^ 2",                                  # right-associative ^
    "1 + 2 * 3 ^ 4 - 5 / 6",                      # full precedence ladder
    "( 1 + 2 ) * 3",                              # parentheses override precedence
    "( ( 1 + 2 ) * ( 3 - 4 ) ) ^ 2",              # nested parentheses
    "sin ( 0 ) + cos ( 9 )",                      # both functions
    "2 + sin ( 5 + 3 ) * 8 + ( 9 * 7 )",          # example from the assignment
]

# Supplementary rejection tests: the target must fail cleanly on these.
REJECTION_TESTS = [
    "( 1 + 2",
    "1 + 2 )",
    "1 # 2",
]


def random_number(rng):
    roll = rng.random()
    if roll < 0.70:
        return str(rng.randint(0, 9))
    if roll < 0.95:
        return str(rng.randint(10, 99))
    return str(rng.randint(100, 999))


# ---------------------------------------------------------------------------
# Expressions the reference implementation can prove correct
# ---------------------------------------------------------------------------
# Every fuzz input is a *correct* arithmetic expression over the required
# operations, so the whole corpus exercises the algorithm itself and never turns
# into a rejection test by accident. Correctness of the generated text is not
# assumed but proved: the text is parsed back with the published grammar
# (precedence "+ -" < "* /" < "^" < "sin cos", "^" right-associative, a function
# applied to a parenthesised argument) and the parsed tree is re-rendered in its
# minimal-parenthesis form. The two token sequences must be identical -- if they
# are not, the expression would mean something else than the tree it came from.

# Nodes produced by the generator. Only these three shapes exist, so a well-formed
# expression cannot be built by accident either.
NUMBER_NODE = "number"
BINARY_NODE = "binary"
FUNCTION_NODE = "function"


def precedence_of(token):
    """Binding strength of a token, exactly as main.cpp sees it."""
    if token == "+" or token == "-":
        return 1
    if token == "*" or token == "/":
        return 2
    if token == "^":
        return 3
    if token in FUNCTIONS:
        return 4
    return 0                          # "(" and anything unknown


def right_associative(token):
    """Only "^" (and functions) are right-associative; everything else is left."""
    return token == "^"


def parse_expression(expression):
    """
    Parse an expression into a tree, or return None when it is not a well-formed
    arithmetic expression over the required operations.

    This is the grammar the generated corpus has to obey, written independently of
    the generator: a factor is a number, a function applied to a parenthesised
    expression, or a parenthesised expression; a chain of binary operators is
    grouped by precedence and associativity.
    """

    def additive(tokens, position):
        """Level 1: "+" and "-", left-associative."""
        node, position = multiplicative(tokens, position)
        if node is None:
            return None, position
        while position < len(tokens) and tokens[position] in ("+", "-"):
            operator = tokens[position]
            right, position = multiplicative(tokens, position + 1)
            if right is None:
                return None, position
            node = (BINARY_NODE, operator, node, right)
        return node, position

    def multiplicative(tokens, position):
        """Level 2: "*" and "/", left-associative."""
        node, position = power(tokens, position)
        if node is None:
            return None, position
        while position < len(tokens) and tokens[position] in ("*", "/"):
            operator = tokens[position]
            right, position = power(tokens, position + 1)
            if right is None:
                return None, position
            node = (BINARY_NODE, operator, node, right)
        return node, position

    def power(tokens, position):
        """
        Level 3: "^", right-associative.

        The right side is parsed at the same level, so "2 ^ 3 ^ 2" becomes
        "2 ^ (3 ^ 2)" -- exactly what the target does for the equal-precedence
        case with a right-associative operator.
        """
        node, position = factor(tokens, position)
        if node is None:
            return None, position
        while position < len(tokens) and tokens[position] == "^":
            right, position = power(tokens, position + 1)
            if right is None:
                return None, position
            node = (BINARY_NODE, "^", node, right)
        return node, position

    def factor(tokens, position):
        if position >= len(tokens):
            return None, position
        token = tokens[position]
        if is_number(token):
            return (NUMBER_NODE, token), position + 1
        if token in FUNCTIONS:
            if position + 1 < len(tokens) and tokens[position + 1] == "(":
                inner, position = parenthesised(tokens, position + 1)
                if inner is None:
                    return None, position
                return (FUNCTION_NODE, token, inner), position
            return None, position
        if token == "(":
            return parenthesised(tokens, position)
        return None, position

    def parenthesised(tokens, position):
        inner, position = additive(tokens, position + 1)
        if inner is None or position >= len(tokens) or tokens[position] != ")":
            return None, position
        return inner, position + 1

    tokens = tokenize(expression)
    if not tokens:
        return None
    tree, position = additive(tokens, 0)
    if tree is None or position != len(tokens):
        return None
    return tree


def render_expression(tree, full):
    """
    Render a tree as text.

    full=True wraps every subexpression in parentheses; full=False emits
    parentheses only where precedence and associativity require them.

    Returns (text, operator) where operator is the token that would bind the
    outermost expression, or None for a value/function. The operator lets a parent
    decide whether it can drop the parentheses of a child.
    """
    kind = tree[0]
    if kind == NUMBER_NODE:
        return tree[1], None
    if kind == FUNCTION_NODE:
        inner, _ = render_expression(tree[2], full)
        return "%s ( %s )" % (tree[1], inner), tree[1]

    operator = tree[1]
    left, left_operator = render_expression(tree[2], full)
    right, right_operator = render_expression(tree[3], full)
    if full:
        return "( %s %s %s )" % (left, operator, right), operator

    outermost = precedence_of(operator)
    # A number or a function call is a single factor and never needs parentheses.
    left_is_factor = tree[2][0] in (NUMBER_NODE, FUNCTION_NODE)
    right_is_factor = tree[3][0] in (NUMBER_NODE, FUNCTION_NODE)

    # An operand needs parentheses while it can be parsed differently than intended
    # (it binds weaker), or when it sits on the side that associativity cannot
    # produce (equal precedence, wrong side).
    if not left_is_factor and (
        precedence_of(left_operator) < outermost
        or (precedence_of(left_operator) == outermost and right_associative(operator))
    ):
        left = "( %s )" % left

    if not right_is_factor and (
        precedence_of(right_operator) < outermost
        or (precedence_of(right_operator) == outermost and not right_associative(operator))
    ):
        right = "( %s )" % right

    return "%s %s %s" % (left, operator, right), operator


def build_expression(rng, max_depth=3):
    """Build a well-formed arithmetic expression tree over the required operations."""

    def build(depth):
        if depth >= max_depth or rng.random() < 0.35:
            return (NUMBER_NODE, random_number(rng))
        # Functions carry a parenthesised argument of their own, so they are rarer
        # than binary operators (7:2 in the original generator, i.e. ~10% of the
        # non-leaf nodes).
        if rng.random() < 0.125:
            return (FUNCTION_NODE, rng.choice(FUNCTIONS), build(depth + 1))
        return (BINARY_NODE, rng.choice(OPERATORS), build(depth + 1), build(depth + 1))

    return build(0)


def random_expression(rng, max_depth=3, parens="mixed"):
    """
    Generate one correct arithmetic expression.

    "full"   -- every subexpression is parenthesised,
    "min"    -- parentheses only where precedence/associativity require them,
    "mixed"  -- either style, chosen per expression (default).

    Returns (expression, style) with style "full" or "min".

    The generator raises AssertionError instead of returning text that does not
    parse back into the tree it came from: a wrong corpus would silently spoil
    every run, so it must fail loudly.
    """
    if parens not in PARENTHESIS_MODES:
        raise ValueError("unknown parenthesis mode: %r" % (parens,))

    tree = build_expression(rng, max_depth)
    full = parens == "full" or (parens == "mixed" and rng.random() < 0.5)
    expression, _operator = render_expression(tree, full)

    tokens = tokenize(expression)
    for token in tokens:
        if not (is_number(token) or token in SUPPORTED_TOKENS):
            raise AssertionError("generator produced unsupported token: %r" % token)

    reparsed = parse_expression(expression)
    if reparsed is None:
        raise AssertionError("generator produced an unparsable expression: %r" % expression)
    if render_expression(reparsed, False)[0] != render_expression(tree, False)[0]:
        raise AssertionError(
            "parentheses do not match the generated tree: %r" % expression
        )
    return expression, ("full" if full else "min")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

CATEGORY_LABELS = {
    "timeout": "no answer (timeout / possible infinite loop)",
    "crash": "process killed by a signal",
    "nonzero_exit": "valid input rejected with a non-zero exit code",
    "bad_format": "output does not follow the expected format",
    "mismatch": "wrong RPN output",
    "unexpected_stderr": "wrote to stderr for a valid input",
    "not_rejected": "invalid input accepted instead of rejected",
    "silent_rejection": "rejected invalid input without an error message",
}


def format_expression(expression):
    return expression if expression.strip() != "" else "<empty input>"


def write_report(path, lines):
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hash and compile C++ sources, then fuzz the executable against a Python oracle."
    )
    parser.add_argument(
        "sources",
        nargs="*",
        metavar="FILE",
        help="C++ files to hash and compile (default: main.cpp next to this script). "
             "Headers are hashed but not passed to the compiler.",
    )
    parser.add_argument(
        "--compiler",
        default=None,
        help="C++ compiler: a path, or a name from PATH (cl, clang-cl, g++, clang++, c++; "
             "default: cl on Windows when Visual Studio is installed, otherwise g++, clang++, c++)",
    )
    parser.add_argument(
        "--cxxflags",
        default=DEFAULT_CXXFLAGS,
        help="compiler flags, GCC-style (default: '%s'); translated to /options for cl.exe" % DEFAULT_CXXFLAGS,
    )
    parser.add_argument(
        "--std",
        default=None,
        metavar="STANDARD",
        help="C++ standard, e.g. c++11, c++17, c++20 (overrides the standard inside --cxxflags; "
             "cl.exe uses /std:c++14 as the closest match for c++11)",
    )
    parser.add_argument(
        "--debug-flag",
        default=DEFAULT_DEBUG_FLAG,
        help="flag appended when a debug build is requested (default: '%s'; cl.exe maps it to /Zi)"
             % DEFAULT_DEBUG_FLAG,
    )
    parser.add_argument(
        "--arch",
        "--msvc-arch",
        dest="arch",
        default=None,
        metavar="ARCH",
        help="target architecture for the Visual Studio environment: x64 (default), x86, arm64",
    )
    parser.add_argument(
        "--vs-install",
        default=None,
        metavar="DIR",
        help="Visual Studio installation to load the cl.exe environment from "
             "(default: vswhere, then the usual install directories)",
    )
    parser.add_argument(
        "--ldflags",
        default="",
        help="extra linker flags (a single string); GCC-style compilers only, cl.exe rejects them",
    )
    parser.add_argument(
        "--output", default=None, help="executable to produce (default: main[.exe] next to the first source)"
    )
    parser.add_argument("--no-build", action="store_true", help="do not compile; test the existing executable")
    parser.add_argument("--binary", default=None, help="existing executable to test (implies --no-build)")
    parser.add_argument(
        "--hash-file",
        action="append",
        default=[],
        metavar="PATH",
        help="extra file to include in the MD5 hash set (repeatable)",
    )
    parser.add_argument("--runs", type=int, default=1000, help="number of fuzz runs (default: 1000)")
    parser.add_argument("--seed", type=int, default=20261012, help="random seed (default: 20261012)")
    parser.add_argument("--max-depth", type=int, default=3, help="max expression nesting for the fuzzer")
    parser.add_argument(
        "--parens",
        choices=PARENTHESIS_MODES,
        default="mixed",
        help="parentheses in generated expressions: 'min' only where precedence requires "
             "them, 'full' around every subexpression, 'mixed' either one per expression "
             "(default: mixed)",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="per-run timeout in seconds")
    parser.add_argument("--max-examples", type=int, default=10, help="failure examples kept in the report")
    parser.add_argument("--report", default="fuzz_report.md", help="report file to write")
    args = parser.parse_args(argv)

    # --- resolve the C++ inputs -------------------------------------------
    sources = [os.path.abspath(path) for path in args.sources]
    if not sources:
        default_source = default_source_path()
        if os.path.isfile(default_source):
            sources = [default_source]
        elif not args.no_build and not args.binary:
            sys.stderr.write(
                "error: no C++ source file given and %s was not found\n"
                "usage: python test.py main.cpp\n" % default_source
            )
            return 2

    for path in sources:
        if not os.path.isfile(path):
            sys.stderr.write("error: source file not found: %s\n" % path)
            return 2

    compile_sources = [path for path in sources if not path.lower().endswith(HEADER_EXTENSIONS)]
    if not compile_sources and not (args.no_build or args.binary):
        sys.stderr.write(
            "error: no compilable C++ source given (need a .cpp/.cc/.cxx file, only headers were listed)\n"
        )
        return 2

    # --- hash every C++ file of the project --------------------------------
    try:
        hash_inputs = collect_cpp_files(sources, args.hash_file)
    except FileNotFoundError as exc:
        sys.stderr.write("error: file to hash not found: %s\n" % exc)
        return 2

    hash_entries = hash_files(hash_inputs, SCRIPT_DIR)
    sources_md5 = aggregate_md5(hash_entries)

    # --- obtain the executable under test ----------------------------------
    build_command = None
    build_warnings = ""
    toolchain_notes = []
    target = None

    if args.binary:
        target = resolve_existing_executable(args.binary)
        if target is None:
            sys.stderr.write("error: executable not found: %s\n" % args.binary)
            return 2
    elif args.no_build:
        candidate = args.output or default_output_path(compile_sources[0] if compile_sources else sources[0])
        target = resolve_existing_executable(candidate)
        if target is None:
            sys.stderr.write(
                "error: --no-build was given but no executable exists at %s\n"
                "build it first or drop --no-build\n" % os.path.abspath(candidate)
            )
            return 2
    else:
        compiler, compiler_kind_value = find_compiler(args.compiler)
        if compiler is None and compiler_kind_value != KIND_MSVC:
            sys.stderr.write(
                "error: no C++ compiler found (looked for cl, clang-cl, g++, clang++ and c++ on PATH)\n"
                "install one or pass --compiler <path> (cl.exe needs Visual Studio; the script\n"
                "finds the installation through vswhere, or use --vs-install <dir>)\n"
            )
            return 2

        # --- the Visual Studio environment (cl.exe / clang-cl.exe) ---------
        build_environment = None
        toolchain_notes = []
        is_msvc = compiler_kind_value in MSVC_KINDS

        if is_msvc:
            install = args.vs_install or find_visual_studio()
            if args.vs_install and not os.path.isdir(args.vs_install):
                sys.stderr.write("error: --vs-install is not a directory: %s\n" % args.vs_install)
                return 2

            if compiler is None:
                # cl was not on PATH: it has to come from the vcvars environment.
                if not install:
                    sys.stderr.write(
                        "error: cl.exe is not on PATH and no Visual Studio installation with the C++\n"
                        "toolchain was found. Install the workload, pass --vs-install <dir>, or run\n"
                        "the script from a 'x64 Native Tools Command Prompt for VS'.\n"
                    )
                    return 2
                compiler = "cl"
                toolchain_notes.append("cl.exe is not on PATH; using the Visual Studio environment")
            elif install:
                toolchain_notes.append("loading the Visual Studio environment for %s" % compiler)

            if install:
                scripts = list(vcvars_scripts(install, args.arch))
                if not scripts:
                    sys.stderr.write(
                        "error: no vcvars*.bat found in %s\n"
                        "is the 'Desktop development with C++' workload installed?\n" % install
                    )
                    return 2

                failure = None
                vcvars_environment = None
                for script, script_args in scripts:
                    _command, environment, info, failure = visual_studio_environment(script, script_args)
                    if failure is None:
                        vcvars_environment = environment
                        toolchain_notes.append("Visual Studio environment: %s" % info[0])
                        for line in info[1:]:
                            toolchain_notes.append("  %s" % line)
                        break
                if failure is not None:
                    sys.stderr.write("error: %s\n" % failure)
                    return 2

                # The captured environment is used verbatim: PATH, INCLUDE and LIB
                # must all come from the selected Visual Studio installation.
                build_environment = vcvars_environment
            elif not shutil.which(compiler) and not os.path.isfile(compiler):
                sys.stderr.write(
                    "error: %s was requested but is not on PATH and no Visual Studio installation was\n"
                    "found to load it from; run from a Developer Command Prompt or pass --vs-install\n"
                    % compiler
                )
                return 2

        # --- flags -----------------------------------------------------------
        flag_list = args.cxxflags.split()
        if args.std:
            wanted = args.std.strip()
            replaced = False
            for position, flag in enumerate(flag_list):
                if flag.lower().startswith(("-std=", "--std=", "/std:")):
                    flag_list[position] = "-std=" + wanted
                    replaced = True
            if not replaced:
                flag_list.append("-std=" + wanted)

        try:
            if is_msvc:
                options, option_notes, dropped = msvc_options(flag_list, args.debug_flag)
            else:
                options, option_notes, dropped = gcc_options(flag_list)
        except ValueError as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 2

        for note in option_notes:
            toolchain_notes.append("flag translation: %s" % note)
        for warning in dropped:
            toolchain_notes.append("warning: %s" % warning)

        if args.ldflags.strip():
            if is_msvc:
                toolchain_notes.append(
                    "warning: --ldflags '%s' ignored (cl.exe links inside the compile step and takes "
                    "/link options; put them in --cxxflags)" % args.ldflags
                )
            else:
                options += args.ldflags.split()

        output = os.path.abspath(args.output) if args.output else default_output_path(compile_sources[0])

        if is_msvc:
            object_file = os.path.join(
                os.path.dirname(output), os.path.splitext(os.path.basename(output))[0] + ".obj"
            )
            build_command, returncode, compiler_out, compiler_err = build_msvc_executable(
                compiler, options, compile_sources, output, object_file, build_environment
            )
        else:
            build_command, returncode, compiler_out, compiler_err = build_executable(
                compiler, options, compile_sources, output, environment=build_environment
            )

        if returncode is None:
            sys.stderr.write("error: could not run %s: %s\n" % (compiler, compiler_err))
            return 2
        if returncode != 0:
            sys.stderr.write("error: compilation failed (exit %d)\n  %s\n" % (returncode, format_command(build_command)))
            if compiler_out.strip():
                sys.stderr.write(compiler_out)
            sys.stderr.write(compiler_err)
            return 2

        build_warnings = compiler_err.strip()
        target = locate_built_executable(output)
        if target is None:
            sys.stderr.write("error: compilation reported success but %s does not exist\n" % output)
            return 2

    # The executable bit does not exist on Windows and os.access() reports X_OK
    # as True for any existing file there, so only check it on POSIX.
    if not IS_WINDOWS and not os.access(target, os.X_OK):
        sys.stderr.write(
            "error: target is not executable: %s\n"
            "fix it with: chmod +x %s\n" % (target, target)
        )
        return 2

    target_md5, target_size = md5_of_file(target)

    rng = random.Random(args.seed)
    started = datetime.datetime.now()

    failures = []          # (corpus, expression, category, expected, actual)
    counts = {}            # category -> count
    corpus_stats = {"bound": 0, "rejection": 0, "fuzz": 0, "min": 0, "full": 0}

    def record(corpus, expression, category, expected, actual):
        if category is None:
            return
        failures.append((corpus, expression, category, expected, actual))
        counts[category] = counts.get(category, 0) + 1

    # --- default boundary tests -------------------------------------------
    bound_results = []
    for expression in DEFAULT_BOUND_TESTS:
        category, expected, actual = check_case(target, expression, args.timeout)
        corpus_stats["bound"] += 1
        bound_results.append((expression, category, expected, actual))
        record("bound", expression, category, expected, actual)

    # --- supplementary rejection tests ------------------------------------
    rejection_results = []
    for expression in REJECTION_TESTS:
        category, expected, actual = check_case(target, expression, args.timeout)
        corpus_stats["rejection"] += 1
        rejection_results.append((expression, category, expected, actual))
        record("rejection", expression, category, expected, actual)

    # --- fuzzing -----------------------------------------------------------
    # Every generated expression is a correct arithmetic expression over the
    # required operations; malformed input is not generated here at all (the
    # REJECTION_TESTS above cover the error paths of the target).
    fuzz_failures_before = len(failures)
    for _ in range(args.runs):
        expression, style = random_expression(rng, max_depth=args.max_depth, parens=args.parens)
        corpus_stats[style] += 1
        category, expected, actual = check_case(target, expression, args.timeout)
        corpus_stats["fuzz"] += 1
        record("fuzz", expression, category, expected, actual)
    fuzz_errors = len(failures) - fuzz_failures_before

    finished = datetime.datetime.now()
    elapsed = (finished - started).total_seconds()

    total_runs = corpus_stats["bound"] + corpus_stats["rejection"] + corpus_stats["fuzz"]
    total_errors = len(failures)
    passed = total_errors == 0

    # --- console output ----------------------------------------------------
    print("Shunting yard fuzzer")
    print("  C++ files     : %d hashed" % len(hash_entries))
    for relative, file_md5, size in hash_entries:
        print("      %-42s %s  (%d B)" % (relative, file_md5, size))
    print("  MD5 (all C++) : %s" % sources_md5)
    if build_command:
        print("  build         : %s" % format_command(build_command))
    else:
        print("  build         : skipped (using existing executable)")
    if toolchain_notes:
        print("  toolchain     : %s" % toolchain_notes[0])
        for line in toolchain_notes[1:]:
            print("                  %s" % line)
    else:
        print("  toolchain     : default settings")
    print("  target        : %s" % target)
    print("  target MD5    : %s  (%d B)" % (target_md5, target_size))
    print("  seed          : %d   (reproducible corpus)" % args.seed)
    print("  bound tests   : %d" % corpus_stats["bound"])
    print("  rejection test: %d" % corpus_stats["rejection"])
    print("  fuzz runs     : %d  (%d minimal parentheses, %d fully parenthesised)"
          % (corpus_stats["fuzz"], corpus_stats["min"], corpus_stats["full"]))
    print("  total runs    : %d" % total_runs)
    print("  errors        : %d" % total_errors)
    print("  elapsed       : %.2fs" % elapsed)
    if build_warnings:
        print("")
        print("Compiler warnings:")
        for line in build_warnings.splitlines():
            print("  %s" % line)
    print("")
    print("Bound test examples:")
    for expression, category, expected, actual in bound_results:
        if category is None:
            shown = ", ".join(expected) if isinstance(expected, list) else str(expected)
            print("  [PASS] %-40s -> %s" % (format_expression(expression), shown))
        else:
            print("  [FAIL] %-40s -> %s (%s)" % (format_expression(expression), category, actual))
    print("")
    if counts:
        print("Error breakdown:")
        for category in sorted(counts):
            print("  %-18s %d  (%s)" % (category, counts[category], CATEGORY_LABELS.get(category, "")))
        print("")
        print("First failures:")
        for corpus, expression, category, expected, actual in failures[: args.max_examples]:
            print("  [%s] %s" % (corpus, format_expression(expression)))
            print("      expected: %s" % (expected,))
            print("      actual  : %s" % (actual,))
        print("")
    print("RESULT: %s" % ("PASS - target matches the reference implementation" if passed else "FAIL - %d error(s) found" % total_errors))

    # --- report file -------------------------------------------------------
    report = []
    report.append("# Shunting yard fuzzing report")
    report.append("")
    report.append("- Generated: %s" % finished.strftime("%Y-%m-%d %H:%M:%S"))
    report.append("- Platform: `%s` (executable suffix `%s`)" % (sys.platform, ".exe" if IS_WINDOWS else "(none)"))
    report.append("- **MD5 of all C++ files: `%s`** (%d files)" % (sources_md5, len(hash_entries)))
    report.append("- Executable: `%s`" % relative_path(target, SCRIPT_DIR))
    report.append("- Executable MD5: `%s` (%d bytes)" % (target_md5, target_size))
    if build_command:
        report.append("- Compiled with: `%s`" % format_command(build_command))
    else:
        report.append("- Compilation skipped (`--no-build`), an existing executable was tested")
    for note in toolchain_notes:
        report.append("- Toolchain: %s" % note)
    report.append("- Reference: Python oracle implementing the same algorithm (tokens `+ - * / ^ sin cos ( ) 0-9`, space separated)")
    report.append("- Random seed: `%d` (re-run with `--seed %d` to reproduce)" % (args.seed, args.seed))
    report.append("- Fuzz runs requested: %d (max nesting depth %d, parentheses: %s)"
                  % (args.runs, args.max_depth, args.parens))
    report.append("- Every generated expression is well-formed: the generator parses its own output back "
                  "with the reference grammar and refuses to continue when the parentheses do not match the tree")
    report.append("- Per-run timeout: %gs" % args.timeout)
    report.append("- Duration: %.2fs" % elapsed)
    report.append("")
    report.append("## Verdict")
    report.append("")
    report.append("**%s**" % ("PASS" if passed else "FAIL"))
    report.append("")
    report.append("| Metric | Value |")
    report.append("| --- | --- |")
    report.append("| Total executions | %d |" % total_runs)
    report.append("| 10 default bound examples | %d |" % corpus_stats["bound"])
    report.append("| Rejection (error handling) tests | %d |" % corpus_stats["rejection"])
    report.append("| Random fuzz runs | %d |" % corpus_stats["fuzz"])
    if corpus_stats["fuzz"]:
        report.append("| - minimal parentheses | %d |" % corpus_stats["min"])
        report.append("| - fully parenthesised | %d |" % corpus_stats["full"])
    report.append("| Errors found | **%d** |" % total_errors)
    report.append("| Fuzz-stage errors | %d |" % fuzz_errors)
    report.append("| Pass rate | %.2f%% |" % (100.0 * (total_runs - total_errors) / total_runs if total_runs else 0.0))
    report.append("")
    report.append("## C++ files hashed (MD5)")
    report.append("")
    report.append("Aggregate MD5 of all C++ files: **`%s`**" % sources_md5)
    report.append("")
    report.append("| File | Size (bytes) | MD5 |")
    report.append("| --- | --- | --- |")
    for relative, file_md5, size in hash_entries:
        report.append("| `%s` | %d | `%s` |" % (relative, size, file_md5))
    report.append("")
    report.append("| Artifact | Size (bytes) | MD5 |")
    report.append("| --- | --- | --- |")
    report.append("| `%s` (executable) | %d | `%s` |" % (relative_path(target, SCRIPT_DIR), target_size, target_md5))
    report.append("")
    report.append("## Default bound test examples")
    report.append("")
    report.append("| # | Input | Expected RPN | Target | Result |")
    report.append("| --- | --- | --- | --- | --- |")
    for position, (expression, category, expected, actual) in enumerate(bound_results, 1):
        if isinstance(expected, list):
            expected_text = " ".join(expected) if expected else "(empty)"
        else:
            expected_text = "reject: %s" % expected
        if category is None:
            actual_text = " ".join(actual) if isinstance(actual, list) and actual else "reject"
            result_text = "PASS"
        else:
            actual_text = str(actual)
            result_text = "FAIL (%s)" % category
        report.append("| %d | `%s` | `%s` | `%s` | %s |"
                      % (position, format_expression(expression), expected_text, actual_text, result_text))
    report.append("")
    report.append("## Error handling tests")
    report.append("")
    report.append("| Input | Expected | Result |")
    report.append("| --- | --- | --- |")
    for expression, category, expected, actual in rejection_results:
        expected_text = "reject: %s" % expected
        result_text = "PASS (exit non-zero)" if category is None else "FAIL (%s)" % category
        report.append("| `%s` | %s | %s |" % (format_expression(expression), expected_text, result_text))
    report.append("")
    report.append("## Error breakdown")
    report.append("")
    if counts:
        report.append("| Category | Count | Meaning |")
        report.append("| --- | --- | --- |")
        for category in sorted(counts):
            report.append("| `%s` | %d | %s |" % (category, counts[category], CATEGORY_LABELS.get(category, "")))
    else:
        report.append("No errors: every execution matched the reference implementation.")
    report.append("")
    if failures:
        report.append("## Failure examples (first %d)" % min(args.max_examples, len(failures)))
        report.append("")
        for corpus, expression, category, expected, actual in failures[: args.max_examples]:
            report.append("- **%s** [%s] `%s`" % (category, corpus, format_expression(expression)))
            report.append("  - expected: `%s`" % (expected,))
            report.append("  - actual: `%s`" % (actual,))
        report.append("")
    report.append("## Notes")
    report.append("")
    report.append("- The MD5 hashes cover every C++ file of the project: the listed sources, all `.cpp`/`.hpp`/`.h` files next to them, and any `--hash-file`.")
    report.append("  The aggregate hash is computed from the sorted `<path>:<md5>` lines, so it changes whenever any file changes, is added, removed or renamed,")
    report.append("  and it does not depend on the order the files were listed in or on the absolute path of the project.")
    report.append("- The executable MD5 identifies the exact binary that was tested, but it may differ between builds of identical sources because some")
    report.append("  linkers (e.g. Mach-O on macOS) embed a random UUID. The aggregate C++ MD5 is the stable identity of the sources.")
    report.append("- The oracle mirrors `main.cpp`: precedence `+ -` < `* /` < `^` < `sin cos`, `^` right-associative, everything else left-associative,")
    report.append("  parentheses group subexpressions, a function directly before `(` is emitted after the group.")
    report.append("- Every fuzz input is a *correct* arithmetic expression over the required operations `+ - * / ^ sin cos ( )` and `0-9`:")
    report.append("  the generator builds an expression tree, renders it, parses the text back with the reference grammar and re-renders it in minimal")
    report.append("  form. Text and tree have to agree, otherwise the generator aborts instead of feeding a wrong corpus to the target.")
    report.append("- Parentheses are emitted only where precedence and associativity require them (`--parens min`), around every subexpression")
    report.append("  (`--parens full`), or either one per expression (`--parens mixed`, the default). Both styles are checked against the same oracle.")
    report.append("- Malformed input is *not* generated: the only rejection cases are the fixed `## Error handling tests` above, which still verify that the")
    report.append("  target reports unknown tokens and mismatched parentheses on stderr and exits non-zero.")
    report.append("- A passing run means the compiled target agrees with the reference on every bound and fuzz case, with no crash, timeout or stray stderr output.")
    if build_warnings:
        report.append("")
        report.append("### Compiler warnings")
        report.append("")
        report.append("```")
        report.append(build_warnings)
        report.append("```")

    report_path = os.path.abspath(args.report)
    write_report(report_path, report)
    print("Report written to %s" % report_path)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
