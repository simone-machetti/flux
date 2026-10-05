#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Author: Simone Machetti
#
# Description:
#   The gate (check) and the stage (measure) of unified-core.problem.yaml; the usage is below.
# -----------------------------------------------------------------------------
"""Check and measure one implementation of the BFP PE grid top_NxN_nr4sd_bfp, at N = 2.

  eval.py check   [DESIGN]   RTL simulation of tb_top_NxN_nr4sd_bfp (Verilator). Prints the
                             testbench's verdict and `N failing of M` (failing = mode x pattern x
                             experiment runs that did not pass); exit 0 pass, 1 wrong, 3 did not
                             build.
  eval.py measure [DESIGN]   Synthesis on ASAP7 (unified-core's Yosys + ABC recipe, flat), STA,
                             the functional testbench again on the netlist (it must pass
                             completely), then tb_top_NxN_nr4sd_bfp_pwr on the netlist for a VCD
                             (its defaults: 100 uniform random operand sets per mode, all 11 modes
                             back to back) and its power (OpenSTA): the average over the whole run,
                             so every mode weighs the same. Prints name=value lines, each figure also as
                             a ratio to imp_v1 measured the same way on this machine. A design
                             given as one file is saved as the next imp_vN/, with its README.md
                             (the agent's documentation, followed by a Results section with the
                             measured figures beside imp_v1's) and metrics.txt.

DESIGN is an imp_vN/ folder (every .sv of the design), or one file holding only the files that
differ from imp_v1/, each section opened by its line `// file: <name>.sv`, plus the version's
documentation in a `// file: README.md` section (what the agent writes); the other files come from
imp_v1/. Default: imp_v1/. The documentation is required, never compiled, and saved as
imp_vN/README.md.

The simulations follow unified-core's scripts/sim and scripts/post-syn-sim (Verilator, the same
flags; asap7_seq_behav.v for the sequential cells); synthesis and STA follow scripts/syn and
scripts/post-syn-sta and post-syn-dpa.

Needs verilator, yosys with yosys-slang ($YOSYS_SLANG_PLUGIN or $YOSYS_SLANG_HOME, else yosys's
own), `sta` or `openroad`, and ASAP7 at $PDK_HOME/vendor/asap7 (or $ASAP7_HOME).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path(__file__).resolve().parent
N = 2
TOP = "top_NxN_nr4sd_bfp"
TB = HOME / "tb_top_NxN_nr4sd_bfp.sv"
TB_PWR = HOME / "tb_top_NxN_nr4sd_bfp_pwr.sv"
SEQ_MODELS = HOME / "asap7_seq_behav.v"
FIXED = {TB.name, TB_PWR.name, SEQ_MODELS.name}
SECTION = re.compile(r"^[ \t]*//[ \t]*file:[ \t]*(\w+\.sv|README\.md)[ \t]*$", re.M)
DOC = "README.md"
LIBS = ["SEQ_RVT_TT_nldm_220123", "SIMPLE_RVT_TT_nldm_211120", "INVBUF_RVT_TT_nldm_220122",
        "AO_RVT_TT_nldm_211120", "OA_RVT_TT_nldm_211120"]
CELLS = ["AO_RVT_TT_201020", "INVBUF_RVT_TT_201020", "SIMPLE_RVT_TT_201020", "OA_RVT_TT_201020"]
ABC = "strash\ndc2\nmap -D {ps} -B 0.9\ntopo\nstime -c\nbuffer -c\nupsize -c\ndnsize -c\n"
VERDICT = re.compile(r"verification: (\d+)/(\d+) \(mode x pattern x experiment\) passed \((\d+) total mismatches\)")


class Failed(Exception):
    """The design did not build or failed a check."""


def setup_error(msg: str) -> None:
    print(f"SETUP ERROR (not the design's fault): {msg}")
    sys.exit(2)


def asap7() -> Path:
    root = os.environ.get("ASAP7_HOME") or os.path.join(os.environ.get("PDK_HOME", ""), "vendor", "asap7")
    if not (Path(root) / "lib" / "NLDM").is_dir():
        setup_error("ASAP7 not found: set PDK_HOME to a checkout of github.com/simone-machetti/asap7-smic-n3-beol")
    return Path(root)


def liberty() -> list[Path]:
    return [asap7() / "lib" / "NLDM" / f"asap7sc7p5t_{name}.lib" for name in LIBS]


def slang() -> str:
    if os.environ.get("YOSYS_SLANG_PLUGIN"):
        return os.environ["YOSYS_SLANG_PLUGIN"]
    if os.environ.get("YOSYS_SLANG_HOME"):
        return os.path.join(os.environ["YOSYS_SLANG_HOME"], "bin", "slang.so")
    return "slang"


def sta_cmd() -> list[str]:
    for tool in ("sta", "openroad"):
        if shutil.which(tool):
            return [tool, "-no_init", "-no_splash", "-exit"]
    setup_error("neither `sta` (OpenSTA) nor `openroad` is on PATH")


def need(*tools: str) -> None:
    for tool in tools:
        if not shutil.which(tool):
            setup_error(f"`{tool}` is not on PATH")


def run(cmd: list, work: Path, log: str, timeout: int = 4 * 3600) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=work, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failed(f"`{cmd[0]}` took more than {timeout} s") from None
    (work / log).write_text(p.stdout + p.stderr)
    return p


def errors(p: subprocess.CompletedProcess, n: int = 12) -> str:
    lines = (p.stdout + p.stderr).splitlines()
    return "\n".join([ln for ln in lines if re.search(r"(?i)error", ln)][:n] or lines[-n:])


def load(design: str | None) -> dict[str, str]:
    """A design as {file name: source}: an imp_vN/ folder, or imp_v1/ with the sections of one file
    put in place of (or beside) its files."""
    base = {p.name: p.read_text() for p in sorted((HOME / "imp_v1").glob("*.sv"))}
    if not design:
        return base
    path = Path(design).resolve()
    if path.is_dir():
        return {p.name: p.read_text() for p in sorted(path.glob("*.sv"))}
    chunks = SECTION.split(path.read_text())
    found = dict(zip(chunks[1::2], chunks[2::2]))
    if not any(name.endswith(".sv") for name in found):
        raise Failed("the file needs one section per changed file, each opened by `// file: <name>.sv`")
    if not found.get(DOC, "").strip():
        raise Failed(f"the file needs the version's documentation in a `// file: {DOC}` section")
    fixed = sorted(set(found) & FIXED)
    if fixed:
        raise Failed(f"{', '.join(fixed)} belong to the evaluation and cannot change")
    doc = {DOC: found.pop(DOC).strip("\n") + "\n"}
    return {**base, **{name: f"// file: {name}{body}" for name, body in found.items()}, **doc}


def write(files: dict[str, str], folder: Path) -> list[Path]:
    """Write every file of the design into folder; the .sv files, for the tools."""
    for name, text in files.items():
        (folder / name).write_text(text)
    return [folder / name for name in files if name.endswith(".sv")]


def results(got: dict[str, float], base: dict[str, float], metrics: dict[str, float], clk_ns: float) -> str:
    """The Results section appended to a version's README.md: its figures beside imp_v1's."""
    rows = [("fmax (MHz)", metrics["fmax_mhz"], 1e6 / base["delay_ps"], metrics["fmax_ratio"]),
            ("area (um2)", got["area_um2"], base["area_um2"], metrics["area_ratio"]),
            ("power (uW)", got["power_uw"], base["power_uw"], metrics["power_ratio"]),
            ("area x power (um2 uW)", got["area_um2"] * got["power_uw"], base["area_um2"] * base["power_uw"],
             metrics["ap_ratio"])]
    missed = [limit for limit, met in (("fmax at least 95% of imp_v1's", metrics["fmax_ratio"] >= 0.95),
                                       ("area at most imp_v1's", metrics["area_ratio"] <= 1.0),
                                       ("power at most imp_v1's", metrics["power_ratio"] <= 1.0)) if not met]
    return "\n".join([
        "", "## Results", "",
        f"Measured by eval.py on the 2 x 2 grid: ASAP7 RVT TT, synthesis and STA at {clk_ns} ns; power from "
        "tb_top_NxN_nr4sd_bfp_pwr on the netlist (100 uniform random operand sets per mode, all 11 modes, "
        f"averaged). tb_top_NxN_nr4sd_bfp passed {int(got['netlist_tests'])}/{int(got['netlist_tests'])} "
        "on the synthesized netlist.", "",
        "| metric | this version | imp_v1 | ratio |", "|---|---|---|---|",
        *(f"| {name} | {value:.4g} | {ref:.4g} | {ratio:.4f} |" for name, value, ref, ratio in rows), "",
        "Limits: " + ("all met." if not missed else "not met: " + "; ".join(missed) + "."), ""])


def save(files: dict[str, str], metrics: str, report: str) -> str:
    """Keep a measured design as the next imp_vN/ (or name the folder that already holds it), its
    README.md completed with the measured results."""
    (HOME / "out").mkdir(exist_ok=True)
    with open(HOME / "out" / "imp.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        versions = sorted(int(d.name[5:]) for d in HOME.glob("imp_v*") if d.is_dir() and d.name[5:].isdigit())
        rtl = {name: text for name, text in files.items() if name.endswith(".sv")}
        for v in versions:
            folder = HOME / f"imp_v{v}"
            if {p.name: p.read_text() for p in folder.glob("*.sv")} == rtl:
                return folder.name
        folder = HOME / f"imp_v{(versions[-1] if versions else 0) + 1}"
        folder.mkdir()
        write({**files, DOC: files.get(DOC, "") + report}, folder)
        (folder / "metrics.txt").write_text(metrics)
        return folder.name


def verilator(sources: list[Path], tb: Path, work: Path, name: str, clk_ns: float, *, gls: bool = False,
              vcd: bool = False) -> str:
    """Build and run one testbench, with unified-core's sim (RTL) or post-syn-sim (netlist) flags."""
    flags = ["-sv", "--build-jobs", "0", "--binary", "--timing", "-Wall", "-Wno-fatal",
             f"-DCLK_PERIOD_NS={clk_ns}", f"-GN={N}"]
    if gls:
        flags += ["--output-split", "20000", "-Wno-SPECIFYIGN", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL",
                  "-Wno-UNDRIVEN", "-DPOST_SYN_SIM", "--x-initial", "fast", "--x-assign", "fast"]
    if vcd:
        flags += ["--trace", "--trace-underscore", "--trace-max-array", "0", "--trace-max-width", "0", "-DVCD"]
        if not gls:
            flags += ["--output-split", "20000"]
    obj = work / f"{name}_obj"
    p = run(["verilator", *flags, "--top-module", tb.stem, *sources, tb, "-Mdir", obj, "-o", "simv"],
            work, f"{name}_build.log")
    if p.returncode:
        raise Failed(f"{name}: did not compile:\n" + errors(p))
    return run([obj / "simv"], work, f"{name}_run.log").stdout


def verdict(out: str) -> tuple[int, int]:
    """(failing, total) from the functional testbench's closing line."""
    m = VERDICT.search(out)
    if not m:
        raise Failed("the functional testbench did not finish:\n" + "\n".join(out.splitlines()[-12:]))
    passed, total, _ = map(int, m.groups())
    return total - passed, total


def cmd_check(args: argparse.Namespace) -> int:
    need("verilator")
    work = Path(tempfile.mkdtemp(prefix="unified-core-check-"))
    try:
        out = verilator(write(load(args.design), work), TB, work, "rtl", 1.0)
        failing, total = verdict(out)
    except Failed as exc:
        print(f"{exc}\n1 failing")
        return 3
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report = [ln for ln in out.splitlines() if re.search(r"===|FAIL|dut=|not held", ln)]
    print("\n".join(report[:40]))
    print(f"{failing} failing of {total}")
    return 0 if failing == 0 else 1


def synthesize(sources: list[Path], work: Path, clk_ps: int) -> float:
    """unified-core's scripts/syn/run.tcl, flat (no KEEP_*): the grid's area in um2."""
    libs = liberty()
    (work / "abc.script").write_text(ABC.format(ps=clk_ps))
    script = [f"plugin -i {slang()}",
              *(f"read_liberty -lib {lib}" for lib in libs),
              f"read_slang --single-unit {' '.join(map(str, sources))} --extern-modules --top {TOP} -G N={N}",
              f"hierarchy -check -top {TOP}", "check", "proc",
              "opt", "fsm", "opt", "memory", "opt", "techmap", "opt",
              f"dfflibmap -liberty {libs[0]}", "opt",
              f"techmap -map {asap7() / 'yoSys' / 'cells_latch_R.v'}", "opt",
              "abc " + " ".join(f"-liberty {lib}" for lib in libs[1:]) + " -script abc.script",
              "opt", "clean",
              "tee -q -o area.json stat -json " + " ".join(f"-liberty {lib}" for lib in libs),
              "flatten", "opt_clean", "rename -hide",
              "write_verilog -noattr -noexpr -nodec netlist.v"]
    (work / "syn.ys").write_text("\n".join(script) + "\n")
    p = run(["yosys", "-s", "syn.ys"], work, "syn.log")
    if p.returncode or not (work / "netlist.v").is_file():
        raise Failed("synthesis failed:\n" + errors(p))
    design = json.loads((work / "area.json").read_text())
    return float(design.get("design", {}).get("area", 0.0))


def sta(work: Path, clk_ps: int) -> tuple[float, float]:
    """unified-core's post-syn-sta and post-syn-dpa: (worst register-to-register delay in ps,
    the grid's power in W from the VCD of tb_top_NxN_nr4sd_bfp_pwr)."""
    tcl = [*(f"read_liberty {lib}" for lib in liberty()),
           "read_verilog netlist.v", f"link_design {TOP}",
           f"create_clock -name clk_i -period {clk_ps} [get_ports clk_i]",
           f"create_clock -name vclk -period {clk_ps}",
           "set data_in {}",
           "foreach port [all_inputs] { if {[lsearch -exact {clk_i rst_ni} [get_property $port full_name]] < 0} "
           "{ lappend data_in $port } }",
           "set_input_delay 0 -clock vclk $data_in",
           "set_false_path -hold -from $data_in",
           "set_output_delay 0 -clock vclk [all_outputs]",
           "set_false_path -hold -to [all_outputs]",
           "report_checks -path_delay max -digits 2 > critical_path.rpt",
           "report_worst_slack -max -digits 3",
           f"read_vcd -scope {TB_PWR.stem}/dut activity.vcd",
           "report_activity_annotation -report_unannotated > unannotated.rpt",
           "report_power -digits 6 > power.rpt"]
    (work / "sta.tcl").write_text("\n".join(tcl) + "\n")
    p = run([*sta_cmd(), "sta.tcl"], work, "sta.log")
    slack = re.search(r"worst slack[^\d-]*(-?\d+(?:\.\d+)?)", p.stdout)
    if p.returncode or not slack or not (work / "power.rpt").is_file():
        raise Failed("STA failed:\n" + errors(p))
    report = (work / "unannotated.rpt").read_text()
    got = re.search(r"^\s*vcd\s+(\d+)", report, re.M)
    miss = re.search(r"^\s*unannotated\s+(\d+)", report, re.M)
    if not (got and miss):
        raise Failed("cannot read the VCD annotation report (unannotated.rpt)")
    if int(miss.group(1)) > 0.05 * (int(got.group(1)) + int(miss.group(1))):
        raise Failed(f"{miss.group(1)} pins have no VCD activity: the power would be estimated, "
                     "not measured (see unannotated.rpt)")
    total = re.search(r"^Total\s+\S+\s+\S+\s+\S+\s+(\S+)", (work / "power.rpt").read_text(), re.M)
    if not total:
        raise Failed("no Total row in power.rpt")
    return clk_ps - float(slack.group(1)), float(total.group(1))


def measure(files: dict[str, str], clk_ns: float) -> dict[str, float]:
    need("verilator", "yosys")
    clk_ps = round(clk_ns * 1000)
    work = Path(tempfile.mkdtemp(prefix="unified-core-measure-"))
    try:
        area = synthesize(write(files, work), work, clk_ps)
        netlist = [SEQ_MODELS, *(asap7() / "verilog" / "stdcell" / f"asap7sc7p5t_{c}.v" for c in CELLS),
                   work / "netlist.v"]
        failing, total = verdict(verilator(netlist, TB, work, "gls", clk_ns, gls=True))
        if failing:
            raise Failed(f"the synthesized netlist fails the functional testbench: {failing} of {total} "
                         "(see gls_run.log)")
        out = verilator(netlist, TB_PWR, work, "pwr", clk_ns, gls=True, vcd=True)
        if "Power stimulus done" not in out:
            raise Failed("the power testbench did not finish (see pwr_run.log)")
        delay, power = sta(work, clk_ps)
    except Failed as exc:
        raise Failed(f"{exc}\n(work directory kept: {work})") from None
    shutil.rmtree(work, ignore_errors=True)
    return {"delay_ps": delay, "area_um2": area, "power_uw": power * 1e6, "netlist_tests": total}


def baseline(clk_ns: float) -> dict[str, float]:
    """imp_v1, measured once per clock and kept in out/ (delete it after changing the tools)."""
    (HOME / "out").mkdir(exist_ok=True)
    path = HOME / "out" / f"imp_v1-{round(clk_ns * 1000)}ps.json"
    with open(HOME / "out" / "baseline.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.is_file():
            try:
                path.write_text(json.dumps(measure(load(None), clk_ns), indent=1))
            except Failed as exc:
                setup_error(f"imp_v1 itself failed: {exc}")
        return json.loads(path.read_text())


def cmd_measure(args: argparse.Namespace) -> int:
    base = baseline(args.clk_ns)
    try:
        files = load(args.design)
        got = measure(files, args.clk_ns)
    except Failed as exc:
        print(exc)
        return 1
    area, power = got["area_um2"] / base["area_um2"], got["power_uw"] / base["power_uw"]
    metrics = {"fmax_ratio": base["delay_ps"] / got["delay_ps"], "area_ratio": area, "power_ratio": power,
               "ap_ratio": area * power, "fmax_mhz": 1e6 / got["delay_ps"], **got}
    lines = "".join(f"{name}={value:.6g}\n" for name, value in metrics.items())
    print(lines, end="")
    if args.design and not Path(args.design).is_dir():
        print(f"saved as {save(files, lines, results(got, base, metrics, args.clk_ns))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="RTL simulation of the functional testbench")
    c.add_argument("design", nargs="?", help="an imp_vN/ folder or one sectioned file (default: imp_v1/)")
    c.set_defaults(fn=cmd_check)
    m = sub.add_parser("measure", help="synthesis, STA, gate-level simulations and power")
    m.add_argument("design", nargs="?", help="an imp_vN/ folder or one sectioned file (default: imp_v1/)")
    m.add_argument("--clk-ns", type=float, default=1.0, help="clock period for synthesis, STA and the testbenches")
    m.set_defaults(fn=cmd_measure)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
