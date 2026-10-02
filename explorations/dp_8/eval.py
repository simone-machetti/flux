#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Author: Simone Machetti
#
# Description:
#   The gate (check) and the stage (measure) of dp_8.problem.yaml; the usage is below.
# -----------------------------------------------------------------------------
"""Check and measure one implementation of the DP8: the gate and the stage of dp_8.problem.yaml.

  eval.py check   [DESIGN]   RTL simulation of the design under tb_top_level.sv (Verilator).
                             Prints the first mismatch and `N failing of M`; exit 0 pass,
                             1 wrong, 3 did not build.
  eval.py measure [DESIGN]   Synthesis on ASAP7 (unified-core's Yosys + ABC recipe), STA,
                             gate-level simulation (Icarus) and VCD power (OpenSTA). Prints
                             name=value lines, each figure also as a ratio to imp_v1 measured the
                             same way on this machine. A design given as one file is saved as
                             the next imp_vN/, with its metrics.txt.

DESIGN is an imp_vN/ folder (top_level.sv, disp_a.sv, disp_b.sv, dp_8.sv), or one file holding
the four, each section opened by its line `// file: top_level.sv` and so on (what the agent
writes). Default: imp_v1/. top_level's ports are fixed by tb_top_level.sv; it holds exactly the
input and output registers (a_i, b_i in, y_o out) and wires u_disp_a, u_disp_b and u_dp_8
between them; the three blocks are combinational.

One DP8 at grid size N costs  dp_8 + (disp_a + disp_b) / N  in area and in power: disp_a and
disp_b sit in the row and column dispatchers, each shared by N PEs; the registers are the same
for every design and are not counted. The delay is the register-to-register path: input
register, dispatcher, dp_8, output register.

Needs verilator, iverilog, yosys with yosys-slang ($YOSYS_SLANG_PLUGIN or $YOSYS_SLANG_HOME, else
yosys's own), `sta` or `openroad`, and ASAP7 at $PDK_HOME/vendor/asap7 (or $ASAP7_HOME).
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
TB = HOME / "tb_top_level.sv"
PARTS = ("top_level", "disp_a", "disp_b", "dp_8")
BLOCKS = {"u_disp_a": "disp_a", "u_disp_b": "disp_b", "u_dp_8": "dp_8"}
SECTION = re.compile(r"^[ \t]*//[ \t]*file:[ \t]*(top_level|disp_a|disp_b|dp_8)\.sv[ \t]*$", re.M)
REGISTER_BITS = 8 * 8 + 8 * 4 + 15
SEQ = re.compile(r"\\?(DFF|DHL|DLL|ICG|SDF)")
LIBS = ["SEQ_RVT_TT_nldm_220123", "SIMPLE_RVT_TT_nldm_211120", "INVBUF_RVT_TT_nldm_220122",
        "AO_RVT_TT_nldm_211120", "OA_RVT_TT_nldm_211120"]
ABC = "strash\ndc2\nmap -D {ps} -B 0.9\ntopo\nstime -c\nbuffer -c\nupsize -c\ndnsize -c\n"


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


def run(cmd: list, work: Path, log: str, timeout: int = 1800) -> subprocess.CompletedProcess:
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
    """A design as {part: source}: an imp_vN/ folder, or one file split at its `// file:` lines."""
    path = Path(design).resolve() if design else HOME / "imp_v1"
    if path.is_dir():
        missing = [f"{part}.sv" for part in PARTS if not (path / f"{part}.sv").is_file()]
        if missing:
            raise Failed(f"{path.name}/ lacks {', '.join(missing)}")
        return {part: (path / f"{part}.sv").read_text() for part in PARTS}
    chunks = SECTION.split(path.read_text())
    preamble, found = chunks[0], dict(zip(chunks[1::2], chunks[2::2]))
    missing = [f"// file: {part}.sv" for part in PARTS if part not in found]
    if missing:
        raise Failed("the file needs one section per module, each opened by its own line: " + ", ".join(missing))
    return {part: f"{preamble}// file: {part}.sv{found[part]}" for part in PARTS}


def write(parts: dict[str, str], work: Path) -> list[Path]:
    files = []
    for part in PARTS:
        (work / f"{part}.sv").write_text(parts[part])
        files.append(work / f"{part}.sv")
    return files


def save(parts: dict[str, str], metrics: str) -> str:
    """Keep a measured design as the next imp_vN/ (or name the folder that already holds it)."""
    (HOME / "out").mkdir(exist_ok=True)
    with open(HOME / "out" / "imp.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        versions = sorted(int(d.name[5:]) for d in HOME.glob("imp_v*") if d.is_dir() and d.name[5:].isdigit())
        for v in versions:
            folder = HOME / f"imp_v{v}"
            if all((folder / f"{p}.sv").is_file() and (folder / f"{p}.sv").read_text() == parts[p] for p in PARTS):
                return folder.name
        folder = HOME / f"imp_v{(versions[-1] if versions else 0) + 1}"
        folder.mkdir()
        write(parts, folder)
        (folder / "metrics.txt").write_text(metrics)
        return folder.name


def cmd_check(args: argparse.Namespace) -> int:
    need("verilator")
    work = Path(tempfile.mkdtemp(prefix="dp_8-check-"))
    try:
        files = write(load(args.design), work)
        p = run(["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style",
                 "--top-module", "tb_top_level", "-Mdir", "obj", *files, TB], work, "build.log")
        if p.returncode:
            raise Failed("did not compile:\n" + errors(p))
        out = run([work / "obj" / "Vtb_top_level"], work, "run.log").stdout
    except Failed as exc:
        print(f"{exc}\n1 failing")
        return 3
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\n".join(ln for ln in out.splitlines() if not ln.startswith("- ")))
    m = re.search(r"(\d+) failing of (\d+)", out)
    if not m:
        print("the testbench did not finish\n1 failing")
        return 3
    return 0 if m.group(1) == "0" else 1


def synthesize(files: list[Path], work: Path, clk_ps: int) -> dict[str, float]:
    """unified-core's scripts/syn/run.tcl, with the three blocks' boundaries kept (KEEP_MODULES)
    so each block's area is its own and nothing merges across dispatcher and PE."""
    libs = liberty()
    (work / "abc.script").write_text(ABC.format(ps=clk_ps))
    keep = " ".join(f"t:{mod}$*" for mod in BLOCKS.values())
    script = [f"plugin -i {slang()}",
              *(f"read_liberty -lib {lib}" for lib in libs),
              f"read_slang --single-unit {' '.join(map(str, files))} --top top_level --keep-hierarchy",
              f"setattr -set keep_hierarchy 1 {keep}",
              "hierarchy -check -top top_level", "check", "proc", "flatten", "opt_clean",
              "opt", "fsm", "opt", "memory", "opt", "techmap", "opt",
              f"dfflibmap -liberty {libs[0]}", "opt",
              f"techmap -map {asap7() / 'yoSys' / 'cells_latch_R.v'}", "opt",
              "abc " + " ".join(f"-liberty {lib}" for lib in libs[1:]) + " -script abc.script",
              "opt", "clean",
              "tee -q -o area.json stat -json " + " ".join(f"-liberty {lib}" for lib in libs),
              "write_verilog -noattr -noexpr -nodec netlist.v"]
    (work / "syn.ys").write_text("\n".join(script) + "\n")
    p = run(["yosys", "-s", "syn.ys"], work, "syn.log")
    if p.returncode or not (work / "netlist.v").is_file():
        raise Failed("synthesis failed:\n" + errors(p))
    stat = json.loads((work / "area.json").read_text())["modules"]
    top = next((v for k, v in stat.items() if k.lstrip("\\") == "top_level"), {}).get("num_cells_by_type", {})
    flops = sum(n for cell, n in top.items() if SEQ.match(cell))
    other = [cell for cell in top if not re.match(r"\\?(DFF|INV|BUF|TIE)", cell)]
    if flops != REGISTER_BITS or other:
        raise Failed(f"top_level must hold only the {REGISTER_BITS} register bits (a_i, b_i in, y_o out) and "
                     f"wire the three blocks; it has {flops} flip-flops and other cells: {', '.join(other) or 'none'}")
    area = {}
    for inst, mod in BLOCKS.items():
        entry = next((v for k, v in stat.items() if k.lstrip("\\").split("$")[0] == mod), None)
        if entry is None:
            raise Failed(f"synthesis kept no module {mod}")
        if any(SEQ.match(cell) for cell in entry.get("num_cells_by_type", {})):
            raise Failed(f"{mod} has flip-flops or latches: the three blocks must be combinational")
        area[inst] = float(entry.get("area", 0.0))
    return area


def bus_bits(work: Path) -> dict[str, int]:
    """The widths of dp_8's inputs, the two buses the dispatchers drive (from the netlist)."""
    text = (work / "netlist.v").read_text()
    body = re.search(r"module\s+\\?dp_8\$\S*\s*\(.*?endmodule", text, re.S)
    widths = {}
    for port in ("a_i", "b_i"):
        m = body and re.search(rf"input\s+\[(\d+):(\d+)\]\s+{port}\s*;", body.group(0))
        widths[f"{port[0]}_bus_bits"] = abs(int(m.group(1)) - int(m.group(2))) + 1 if m else 0
    return widths


def simulate_gls(work: Path) -> None:
    """The netlist under the same testbench (Icarus), one vector per ns, activity.vcd."""
    stdcell = asap7() / "verilog" / "stdcell"
    cells = [*sorted(stdcell.glob("asap7sc7p5t_*_RVT_TT_201020.v")), stdcell / "asap7sc7p5t_SEQ_RVT_TT_220101.v"]
    p = run(["iverilog", "-g2012", "-DGLS", "-DVCD", "-s", "tb_top_level", "-o", "gls.vvp",
             *cells, "netlist.v", TB], work, "gls_build.log")
    if p.returncode:
        raise Failed("gate-level simulation did not compile:\n" + errors(p))
    p = run(["vvp", "-n", "gls.vvp"], work, "gls_run.log")
    m = re.search(r"(\d+) failing of (\d+)", p.stdout)
    if not m or m.group(1) != "0":
        raise Failed("the synthesized netlist fails the testbench:\n" + p.stdout[-1500:])


def sta(work: Path, clk_ps: int) -> tuple[float, dict[str, float]]:
    """unified-core's post-syn-sta and post-syn-dpa: (worst path delay in ps, power per block in W)."""
    tcl = [*(f"read_liberty {lib}" for lib in liberty()),
           "read_verilog netlist.v", "link_design top_level",
           f"create_clock -name clk -period {clk_ps} [get_ports clk_i]",
           "set data_in {}",
           "foreach port [all_inputs] { if {[lsearch -exact {clk_i rst_ni} [get_property $port full_name]] < 0} "
           "{ lappend data_in $port } }",
           "set_input_delay 0 -clock clk $data_in",
           "set_output_delay 0 -clock clk [all_outputs]",
           "set_false_path -from [get_ports rst_ni]",
           "report_checks -path_delay max -digits 2 > critical_path.rpt",
           "report_worst_slack -max -digits 3",
           "read_vcd -scope tb_top_level/dut activity.vcd",
           "report_activity_annotation -report_unannotated > unannotated.rpt",
           "report_power -digits 6 -instances [get_cells {" + " ".join(BLOCKS) + "}] > power.rpt"]
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
    if int(miss.group(1)) > 0.02 * (int(got.group(1)) + int(miss.group(1))):
        raise Failed(f"{miss.group(1)} pins have no VCD activity: the power would be estimated, "
                     "not measured (see unannotated.rpt)")
    power = {}
    text = (work / "power.rpt").read_text()
    for inst in BLOCKS:
        row = re.search(rf"^\s*(?:[-\d.eE+]+\s+){{3}}([-\d.eE+]+)\s+{inst}\s*$", text, re.M)
        if not row:
            raise Failed(f"no power row for {inst} in power.rpt")
        power[inst] = float(row.group(1))
    return clk_ps - float(slack.group(1)), power


def measure(parts: dict[str, str], n: int, clk_ps: int) -> dict[str, float]:
    need("yosys", "iverilog", "vvp")
    work = Path(tempfile.mkdtemp(prefix="dp_8-measure-"))
    try:
        area = synthesize(write(parts, work), work, clk_ps)
        buses = bus_bits(work)
        simulate_gls(work)
        delay, power = sta(work, clk_ps)
    except Failed as exc:
        raise Failed(f"{exc}\n(work directory kept: {work})") from None
    shutil.rmtree(work, ignore_errors=True)
    uw = {inst: w * 1e6 for inst, w in power.items()}
    return {"delay_ps": delay,
            "area_um2": area["u_dp_8"] + (area["u_disp_a"] + area["u_disp_b"]) / n,
            "power_uw": uw["u_dp_8"] + (uw["u_disp_a"] + uw["u_disp_b"]) / n,
            "area_dp_8_um2": area["u_dp_8"], "area_disp_a_um2": area["u_disp_a"],
            "area_disp_b_um2": area["u_disp_b"], "power_dp_8_uw": uw["u_dp_8"],
            "power_disp_a_uw": uw["u_disp_a"], "power_disp_b_uw": uw["u_disp_b"], **buses}


def baseline(n: int, clk_ps: int) -> dict[str, float]:
    """imp_v1, measured once per setting and kept in out/ (delete it after changing the tools)."""
    (HOME / "out").mkdir(exist_ok=True)
    path = HOME / "out" / f"imp_v1-n{n}-{clk_ps}ps.json"
    with open(HOME / "out" / "baseline.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.is_file():
            try:
                path.write_text(json.dumps(measure(load(None), n, clk_ps), indent=1))
            except Failed as exc:
                setup_error(f"imp_v1 itself failed: {exc}")
        return json.loads(path.read_text())


def cmd_measure(args: argparse.Namespace) -> int:
    clk_ps = round(args.clk_ns * 1000)
    base = baseline(args.n, clk_ps)
    try:
        parts = load(args.design)
        got = measure(parts, args.n, clk_ps)
    except Failed as exc:
        print(exc)
        return 1
    area, power = got["area_um2"] / base["area_um2"], got["power_uw"] / base["power_uw"]
    metrics = {"fmax_ratio": base["delay_ps"] / got["delay_ps"], "area_ratio": area, "power_ratio": power,
               "ap_ratio": area * power, "fmax_mhz": 1e6 / got["delay_ps"], **got}
    lines = "".join(f"{name}={value:.6g}\n" for name, value in metrics.items())
    print(lines, end="")
    if args.design and not Path(args.design).is_dir():
        print(f"saved as {save(parts, lines)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="RTL simulation under tb_top_level.sv")
    c.add_argument("design", nargs="?", help="an imp_vN/ folder or one sectioned file (default: imp_v1/)")
    c.set_defaults(fn=cmd_check)
    m = sub.add_parser("measure", help="synthesis, STA, gate-level simulation and power")
    m.add_argument("design", nargs="?", help="an imp_vN/ folder or one sectioned file (default: imp_v1/)")
    m.add_argument("--n", type=int, default=8, help="PEs sharing each dispatcher (the grid size)")
    m.add_argument("--clk-ns", type=float, default=1.0, help="clock period for synthesis and STA (the testbench applies one vector per ns)")
    m.set_defaults(fn=cmd_measure)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
