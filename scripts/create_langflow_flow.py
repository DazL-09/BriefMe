"""Buat/update flow Langflow "BriefME" — dua engine (--engine bob|gemini, default bob).

Engine bob (utama — arsitektur hackathon: Bob + Langflow, MCP di sisi Bob):
    6 node / 5 edge:
        Chat Input ──→ Prompt ──→ Python Interpreter ──→ Parse JSON ──→ Parser ──→ Chat Output
        (jurnal)      (kode       (subprocess bob run    (jq: ambil      (format    "Thought Record"
                       CBT +       via API resmi,         last_message)   {text})
                       {text})     tool MCP lewat
                                   .bob/mcp.json)
    Python Interpreter memanggil `bob run --mode agent --format json` (kunci dari
    .env BOB_API_KEY); Bob berbicara ke MCP hearmeout-tools (mcp_server.py :8788)
    lewat .bob/mcp.json sehingga dokumen .docx dibuat oleh Bob sendiri.

Engine gemini (cadangan — arsitektur lama, MCP lewat node Langflow):
    5 node / 4 edge: Chat Input → Agent (+Google Generative AI + MCP Tools) → Chat Output.
    Flow ini disimpan terpisah "BriefME (Gemini)" endpoint briefme-gemini; saat pertama
    kali engine bob dijalankan, flow Gemini yang ada di-CLONE ke sana sebelum data
    primary (endpoint briefme) ditimpa dengan data bob.

Catatan implementasi (mengikuti lfx.graph.flow_builder.connect):
- Node sumber tool harus "flipped" ke tool mode: template.tool_mode=True dan
  outputs diganti output sintetis component_as_tool (method to_toolkit).
- Edge memakai id reactflow__edge-… dan handle string dengan œ (U+0153)
  sebagai pengganti kutip — format yang dipakai kanvas & connect.py resmi.
- Prompt template adalah KODE python (str.format, satu-satunya variabel {text});
  output Prompt (Message berisi kode ter-render) menimpa field python_code di
  runtime — nilai tersimpan di template hanya untuk tampilan UI.
- Python Interpreter sandbox: TANPA pernyataan import dalam kode (validate_code_safety
  menolak ast.Import); module datang dari field global_imports ("math,os,subprocess")
  dan builtins tidak memuat open() — fallback kunci BOB dibaca via subprocess cat.

Pemakaian:
    set -a; . ./.env; set +a
    python scripts/create_langflow_flow.py                # engine bob (default)
    python scripts/create_langflow_flow.py --engine gemini  # flow cadangan Gemini
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def lf_url() -> str:
    """URL Langflow — dibaca per pemanggilan agar .env sudah ter-muat dulu."""
    return os.environ.get("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")

FLOW_NAME = "BriefME"
ENDPOINT = "briefme"
# Nama flow lama (arsitektur Bob / sensor) — dipakai untuk menemukan flow yang
# sudah ada agar di-PATCH, bukan membuat flow baru.
LEGACY_FLOW_NAMES = ("HearMeOut Sensor Anonymizer", "BriefME")

# MCP server tool dokumen (Tahap 3 — mcp_server.py). Didaftarkan ke Langflow
# lewat API v2 + disimpan sebagai fallback di node MCP Tools.
MCP_SERVER_NAME = "hearmeout-tools"
MCP_SERVER_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8788/mcp")

# Model Gemini untuk node Agent (dan field model node Google Generative AI).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Field api_key node Google Generative AI memakai load_from_db=true — nilainya
# adalah NAMA variabel global Langflow (bukan kunci literal). Builder menyinkronkan
# nilai .env → variabel ini lewat API (dienkripsi di database Langflow).
GEMINI_VAR_NAME = "GOOGLE_API_KEY"

# ---- Engine Bob (utama) vs Gemini (cadangan) --------------------------------
# Primary: "BriefME" endpoint briefme (data bob bila engine bob pernah jalan).
FALLBACK_FLOW_NAME = "BriefME (Gemini)"
FALLBACK_ENDPOINT = "briefme-gemini"
FLOW_ID_GEMINI = ".flow_id.gemini"

DESC_BOB = ("BriefME — Thought Record CBT dari jurnal pasien "
            "(Langflow orkestrasi + Bob CLI via API resmi, MCP di sisi Bob)")
DESC_GEMINI = ("BriefME (Gemini) — cadangan arsitektur Gemini "
               "(Langflow Agent + MCP hearmeout-tools)")

BOB_IDS = {
    "chat_input": "ChatInput-HEARB1",
    "prompt": "Prompt-HEARB2",
    "python": "PythonREPL-HEARB3",
    "parse_json": "ParseJSONData-HEARB4",
    "parser": "Parser-HEARB5",
    "chat_output": "ChatOutput-HEARB6",
}

# Ambil last_message dari output `bob run --format json` (String.data stdout),
# tahan terhadap noise sebelum JSON dan pagar code fence; sudah lolos
# validate_jq_program Langflow + 8 kasus uji (murni/noise/fence/gagal/kosong/…).
JQ_LAST_MESSAGE = (
    '(.result // "BOB_GAGAL: output kosong") '
    '| (try (fromjson | .last_message) catch null) '
    '// (try (capture("(?<j>\\\\{.*") | .j | fromjson | .last_message) catch null) '
    '// . '
    '| if type == "string" then (gsub("```[a-z]*"; "") | ltrimstr("\\n")) else tostring end'
)

IDs = {
    "chat_input": "ChatInput-HEAR1",
    "gemini": "GoogleGenAI-HEAR2",
    "mcp_tools": "MCPTools-HEAR3",
    "agent": "Agent-HEAR4",
    "chat_output": "ChatOutput-HEAR5",
}

# Instruksi CBT untuk node Agent (diadaptasi dari prompt clinical-copilot lama;
# tanpa jargon bob/tools-call CLI — Langflow yang menggerakkan tool calling).
AGENT_SYSTEM_PROMPT = """\
Anda adalah Clinical Copilot dari HearMeOut. Anda menerima teks jurnal pasien
(tanpa data pribadi) dan tugas Anda mengekstraknya ke matriks CBT lalu membuat
dokumen lewat tool yang tersedia.

Langkah wajib Anda:
1. Analisis teks dan ekstrak ke dalam format JSON dengan kunci:
   - situation (Situasi/kejadian pemicu)
   - thought (Pikiran otomatis yang muncul saat kejadian)
   - emotion (Emosi yang dirasakan)
   - behavior (Perilaku yang dilakukan)
   - alternate_thought (Pikiran alternatif yang lebih seimbang — hasil
     restrukturisasi kognitif ringan, bukan kutipan dari teks)
2. Setelah ekstraksi selesai, panggil tool 'generate_clinical_document'
   dengan mengirimkan data JSON tersebut (tepat satu kali per teks masuk).
3. Setelah tool sukses, akhiri pesan HANYA dengan format berikut (tanpa teks lain):
   | Situasi | Pikiran | Emosi | Perilaku | Pikiran Alternatif |
   |---|---|---|---|---|
   | ... | ... | ... | ... | ... |
   (satu baris data per situasi; bila teks memuat beberapa situasi, buat beberapa baris)

   Lalu satu baris kosong dan satu baris: Dokumen: <path file dokumen>

Aturan:
- Jangan berikan saran medis kepada pengguna. Fokus pada ekstraksi data secara objektif.
- Asumsikan teks jurnal tidak memuat data pribadi; jangan bertanya mengenai identitas pasien.
- Ekstraksi harus objektif: kutip/parafrase keluhan apa adanya, tanpa diagnosa.
- Bila panggilan tool gagal, ulangi tepat satu kali, lalu laporkan error apa adanya —
  jangan pernah menjawab "timeout" atau "server tidak tersedia" tanpa mencoba tool.
- Bila teks tidak memuat keluhan yang bisa diekstrak, tetap buat tabel dengan sel "-".
"""


def load_env_file(path: Path) -> None:
    """Muat KEY=VALUE sederhana dari .env tanpa menimpa env yang sudah ada."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api(method: str, path: str, payload: dict | list | None = None) -> dict | list:
    """Panggil API Langflow. Kembalikan JSON; error HTTP membawa body pesan."""
    req = urllib.request.Request(
        lf_url() + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ.get("LANGFLOW_API_KEY", ""),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:2000]}")
    if raw[:2] == b"\x1f\x8b":  # server dapat mengirim gzip
        import gzip

        raw = gzip.decompress(raw)
    body = raw.decode()
    return json.loads(body) if body.strip() else {}


def catalog_entry(catalog: dict, group: str, key: str) -> dict:
    entry = catalog.get(group, {}).get(key)
    if not entry:
        raise SystemExit(f"Komponen {group}/{key} tidak ada di katalog Langflow")
    return deepcopy(entry)


def node(entry: dict, type_key: str, node_id: str, pos: list[int]) -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": pos,
        "data": {"node": entry, "type": type_key, "id": node_id},
    }


def scaped_json(obj: dict) -> str:
    """Serialisasi handle ala lfx (kompak, kutip diganti œ U+0153)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).replace('"', "œ")


def edge(
    src: str,
    src_out: str,
    src_types: list[str],
    src_type_key: str,
    dst: str,
    dst_field: str,
    dst_types: list[str],
    dst_type: str,
) -> dict:
    source_handle = {
        "dataType": src_type_key,
        "id": src,
        "name": src_out,
        "output_types": src_types,
    }
    target_handle = {
        "fieldName": dst_field,
        "id": dst,
        "inputTypes": dst_types,
        "type": dst_type,
    }
    source_s = scaped_json(source_handle)
    target_s = scaped_json(target_handle)
    return {
        "animated": False,
        "className": "",
        "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
        "id": f"reactflow__edge-{src}{source_s}-{dst}{target_s}",
        "selected": False,
        "source": src,
        "sourceHandle": source_s,
        "target": dst,
        "targetHandle": target_s,
    }


def flip_tool_mode(entry: dict) -> None:
    """Aktifkan tool mode pada node sumber tool (mengikuti _enable_tool_mode lfx).

    template.tool_mode = True dan outputs diganti satu output sintetis
    component_as_tool (method to_toolkit, selected Tool).
    """
    entry["tool_mode"] = True
    entry["outputs"] = [
        {
            "allows_loop": False,
            "cache": True,
            "display_name": "Toolset",
            "group_outputs": False,
            "hidden": False,
            "method": "to_toolkit",
            "name": "component_as_tool",
            "options": None,
            "required_inputs": None,
            "selected": "Tool",
            "tool_mode": True,
            "types": ["Tool"],
            "value": "__UNDEFINED__",
        }
    ]


def register_mcp_server() -> None:
    """Daftarkan MCP server tool dokumen ke Langflow (API v2), idempoten."""
    body = {"url": MCP_SERVER_URL}
    try:
        api("GET", f"/api/v2/mcp/servers/{MCP_SERVER_NAME}")
        api("PATCH", f"/api/v2/mcp/servers/{MCP_SERVER_NAME}", body)
        print(f"MCP server terdaftar (diupdate): {MCP_SERVER_NAME} → {MCP_SERVER_URL}")
    except SystemExit:
        api("POST", f"/api/v2/mcp/servers/{MCP_SERVER_NAME}", body)
        print(f"MCP server terdaftar (baru): {MCP_SERVER_NAME} → {MCP_SERVER_URL}")


def sync_gemini_variable(key: str) -> bool:
    """Sinkronkan GOOGLE_API_KEY (.env) ke variabel global Langflow.

    Variabel bernama provider (GOOGLE_API_KEY) ditolak oleh PATCH (404) —
    strateginya: hapus lalu buat ulang (POST). CATATAN: Langflow mem-validasi
    kunci provider dengan memanggil model sungguhan saat disimpan, jadi kunci
    placeholder ('xxx') ditolak 400 — hanya kunci asli yang bisa tersimpan.

    Mengembalikan True bila variabel kini punya nilai asli.
    """
    variables = api("GET", "/api/v1/variables/")
    items = variables if isinstance(variables, list) else variables.get("data", [])
    existing = next((v for v in items if v.get("name") == GEMINI_VAR_NAME), None)

    if key == "xxx":
        if existing and existing.get("has_value"):
            print(f"Variabel {GEMINI_VAR_NAME} di Langflow sudah terisi nilai asli — "
                  "placeholder 'xxx' diabaikan.")
            return True
        print(f"PERINGATAN: {GEMINI_VAR_NAME} masih placeholder 'xxx' dan variabel "
              "Langflow belum terisi — flow tetap dibuat, semua run akan gagal di "
              "panggilan Gemini sampai kunci asli diisi di .env lalu skrip dijalankan ulang.")
        return False

    payload = {"name": GEMINI_VAR_NAME, "value": key, "type": "Credential",
               "default_fields": ["api_key"]}
    if existing:
        # PATCH ditolak (404) untuk variabel provider — hapus + buat ulang.
        try:
            api("DELETE", f"/api/v1/variables/{existing['id']}")
        except SystemExit as e:
            print(f"Catatan: gagal menghapus variabel lama ({e}); lanjut POST.")
    try:
        api("POST", "/api/v1/variables/", payload)
    except SystemExit as e:
        sys.exit(
            f"Gagal menyimpan variabel {GEMINI_VAR_NAME} ke Langflow.\n"
            f"  {e}\n"
            "  (Langflow mem-validasi kunci provider secara live — pastikan kunci "
            "Gemini asli, bukan placeholder.)"
        )
    print(f"Variabel {GEMINI_VAR_NAME} disinkronkan dari .env (validasi live lolos).")
    return True


def build_python_code_template() -> str:
    """Susun kode Python node Python Interpreter (template dengan {text}).

    Kode dijalankan sandbox Langflow: tanpa pernyataan import (validate_code_safety
    menolak ast.Import — module dari global_imports), tanpa open() (builtins tak
    memilikinya) — fallback kunci BOB dibaca via `subprocess cat .env`.
    Satu-satunya kurung kurawal di seluruh kode adalah placeholder {text} yang
    dirender oleh node Prompt (str.format).
    """
    instr = AGENT_SYSTEM_PROMPT
    assert "{" not in instr and "}" not in instr, "AGENT_SYSTEM_PROMPT memuat kurung kurawal"
    assert "'''" not in instr, "AGENT_SYSTEM_PROMPT memuat tiga kutip"
    code = (
        f"PROJECT = {str(BASE)!r}\n"
        "BOB = os.path.expanduser('~/.local/share/npm/bin/bob')\n"
        "env = os.environ.copy()\n"
        "if not env.get('BOB_API_KEY'):\n"
        "    try:\n"
        "        cat = subprocess.run(['cat', PROJECT + '/.env'], capture_output=True, text=True, timeout=10)\n"
        "        for ln in cat.stdout.splitlines():\n"
        "            if ln.startswith('BOB_API_KEY='):\n"
        "                env['BOB_API_KEY'] = ln.split('=', 1)[1]\n"
        "            if ln.startswith('BOB_MAX_TURNS='):\n"
        "                env['BOB_MAX_TURNS'] = ln.split('=', 1)[1]\n"
        "    except Exception as e:\n"
        "        print('BOB_GAGAL env: %s' % e)\n"
        "if not env.get('BOB_MAX_TURNS'):\n"
        "    env['BOB_MAX_TURNS'] = '6'\n"
        "INSTR = '''\n" + instr + "\n'''\n"
        "prompt = INSTR + '\\n\\nJurnal pasien (teks pengguna):\\n' + '''{text}'''\n"
        "try:\n"
        "    p = subprocess.run([BOB, 'run', '--accept-license', '--mode', 'agent', '--format', 'json', "
        "'--log-level', 'error', '--trust', '--workspace', PROJECT, '--max-turns', env.get('BOB_MAX_TURNS', '6'), "
        "'--disable-subagents', prompt], input='', capture_output=True, text=True, timeout=900, env=env, cwd=PROJECT)\n"
        "    out = p.stdout.strip()\n"
        "    if out:\n"
        "        print(out)\n"
        "    else:\n"
        "        print('BOB_GAGAL rc=%d %s' % (p.returncode, p.stderr.strip()[:400]))\n"
        "except Exception as e:\n"
        "    print('BOB_GAGAL: %s' % e)\n"
    )
    rest = code.replace("{text}", "")
    assert "{" not in rest and "}" not in rest, "template memuat kurung kurawal selain {text}"
    return code


def build_flow_bob() -> dict:
    """Flow 6 node / 5 edge — engine Bob (arsitektur utama hackathon)."""
    catalog = api("GET", "/api/v1/all")

    chat_in = catalog_entry(catalog, "input_output", "ChatInput")
    prompt = catalog_entry(catalog, "models_and_agents", "Prompt Template")
    python_repl = catalog_entry(catalog, "utilities", "PythonREPLComponent")
    parse_json = catalog_entry(catalog, "processing", "ParseJSONData")
    parser = catalog_entry(catalog, "processing", "ParserComponent")
    chat_out = catalog_entry(catalog, "input_output", "ChatOutput")

    code_template = build_python_code_template()

    # --- Prompt: template = kode python bob; field dinamis {text} sebagai tujuan
    # edge Chat Input (bentuk field mengikuti DefaultPromptField.to_dict()).
    prompt["template"]["template"]["value"] = code_template
    prompt["template"]["text"] = {
        "field_type": "str", "required": False, "placeholder": "", "list": False,
        "show": True, "multiline": True, "value": "", "fileTypes": [], "file_path": "",
        "name": "text", "display_name": "text", "advanced": False, "api_editable": False,
        "input_types": ["Message"], "dynamic": False, "info": "", "load_from_db": False,
        "title_case": False, "type": "str",
    }
    prompt["custom_fields"] = {"template": ["text"]}
    if "text" not in prompt.get("field_order", []):
        prompt["field_order"] = list(prompt.get("field_order", [])) + ["text"]

    # --- Python Interpreter: sandbox global_imports + kode template (nilai ini
    # ditimpa output Prompt saat run; disimpan agar UI menampilkan rantai jelas).
    python_repl["template"]["python_code"]["value"] = code_template
    python_repl["template"]["global_imports"]["value"] = "math,os,subprocess"

    # --- Parse JSON: jq ambil last_message dari output bob run --format json
    parse_json["template"]["query"]["value"] = JQ_LAST_MESSAGE

    # --- Parser: format tabel dari baris Data (satu Data per baris jq)
    parser["template"]["pattern"]["value"] = "{text}"

    # --- Chat Output: "Thought Record" (tabel Markdown + baris Dokumen: <path>)
    chat_out["display_name"] = "Thought Record"

    nodes = [
        node(chat_in, "ChatInput", BOB_IDS["chat_input"], [0, 300]),
        node(prompt, "Prompt Template", BOB_IDS["prompt"], [360, 300]),
        node(python_repl, "PythonREPLComponent", BOB_IDS["python"], [940, 300]),
        node(parse_json, "ParseJSONData", BOB_IDS["parse_json"], [1520, 300]),
        node(parser, "ParserComponent", BOB_IDS["parser"], [1980, 300]),
        node(chat_out, "ChatOutput", BOB_IDS["chat_output"], [2380, 300]),
    ]
    edges = [
        edge(BOB_IDS["chat_input"], "message", ["Message"], "ChatInput",
             BOB_IDS["prompt"], "text", ["Message"], "str"),
        edge(BOB_IDS["prompt"], "prompt", ["Message"], "Prompt Template",
             BOB_IDS["python"], "python_code", ["Message"], "str"),
        edge(BOB_IDS["python"], "results", ["JSON"], "PythonREPLComponent",
             BOB_IDS["parse_json"], "input_value", ["Message", "Data", "JSON"], "other"),
        edge(BOB_IDS["parse_json"], "filtered_data", ["JSON"], "ParseJSONData",
             BOB_IDS["parser"], "input_data", ["DataFrame", "Table", "Data", "JSON"], "other"),
        edge(BOB_IDS["parser"], "parsed_text", ["Message"], "ParserComponent",
             BOB_IDS["chat_output"], "input_value",
             ["Data", "JSON", "DataFrame", "Table", "Message"], "other"),
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def build_flow_gemini() -> dict:
    catalog = api("GET", "/api/v1/all")

    chat_in = catalog_entry(catalog, "input_output", "ChatInput")
    gemini = catalog_entry(catalog, "google", "ext:google:GoogleGenerativeAIComponent@official")
    mcp_tools = catalog_entry(catalog, "models_and_agents", "MCPTools")
    agent = catalog_entry(catalog, "models_and_agents", "Agent")
    chat_out = catalog_entry(catalog, "input_output", "ChatOutput")

    # --- Google Generative AI: model Gemini; api_key = nama variabel global
    # Langflow (field load_from_db) — nilainya disinkronkan builder dari .env.
    gemini["template"]["api_key"]["value"] = GEMINI_VAR_NAME
    gemini["template"]["model_name"]["value"] = GEMINI_MODEL
    # Wajib untuk tool calling Gemini (langchain-google-genai).
    gemini["template"]["tool_model_enabled"]["value"] = True

    # --- MCP Tools: hearmeout-tools (generate_clinical_document → .docx)
    mcp_tools["template"]["mcp_server"]["value"] = {
        "name": MCP_SERVER_NAME,
        "config": {"url": MCP_SERVER_URL},
    }
    mcp_tools["template"]["headers"]["value"] = {}
    mcp_tools["template"]["tool"]["value"] = ""  # semua tool (generate_clinical_document)
    mcp_tools["template"]["tool_execution_timeout"]["value"] = 60.0
    flip_tool_mode(mcp_tools)

    # --- Agent: otak CBT — instruksi + tool dari MCP Tools, model dari Gemini
    agent["template"]["system_prompt"]["value"] = AGENT_SYSTEM_PROMPT
    agent["template"]["add_calculator_tool"]["value"] = False
    agent["template"]["add_current_date_tool"]["value"] = False

    # --- Chat Output: "Thought Record" (tabel Markdown + baris Dokumen)
    chat_out["display_name"] = "Thought Record"

    nodes = [
        node(chat_in, "ChatInput", IDs["chat_input"], [0, 320]),
        node(gemini, "ext:google:GoogleGenerativeAIComponent@official", IDs["gemini"], [420, 0]),
        node(mcp_tools, "MCPTools", IDs["mcp_tools"], [420, 640]),
        node(agent, "Agent", IDs["agent"], [980, 320]),
        node(chat_out, "ChatOutput", IDs["chat_output"], [1500, 320]),
    ]
    edges = [
        edge(IDs["chat_input"], "message", ["Message"], "ChatInput",
             IDs["agent"], "input_value", ["Message"], "str"),
        edge(IDs["gemini"], "model_output", ["LanguageModel"],
             "ext:google:GoogleGenerativeAIComponent@official",
             IDs["agent"], "model", ["LanguageModel"], "model"),
        edge(IDs["mcp_tools"], "component_as_tool", ["Tool"], "MCPTools",
             IDs["agent"], "tools", ["Tool"], "other"),
        edge(IDs["agent"], "response", ["Message"], "Agent",
             IDs["chat_output"], "input_value",
             ["Data", "JSON", "DataFrame", "Table", "Message"], "other"),
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def find_existing_flow() -> dict | None:
    """Cari flow lama supaya di-PATCH (bukan membuat flow baru)."""
    flow_id_path = BASE / ".flow_id"
    if flow_id_path.exists():
        flow_id = flow_id_path.read_text().strip()
        if flow_id:
            try:
                flow = api("GET", f"/api/v1/flows/{flow_id}")
                if isinstance(flow, dict) and flow.get("id"):
                    return flow
            except SystemExit:
                pass  # id sudah tidak ada — lanjut cari by nama
    for legacy in LEGACY_FLOW_NAMES:
        flows = api("GET", f"/api/v1/flows/?name={legacy}")
        if isinstance(flows, list):
            found = next((f for f in flows if f.get("name") == legacy), None)
            if found:
                return found
    return None


def is_gemini_style(flow: dict) -> bool:
    """True bila data flow memuat node Google Generative AI (arsitektur cadangan).

    Pakai "GoogleGenerativeAI" (nama komponen) — jangan "GoogleGenAI": itu bukan
    substring dari "GoogleGenerativeAIComponent" (ada huruf 'e' setelah 'Gen').
    """
    return "GoogleGenerativeAI" in json.dumps(flow.get("data") or {})


def find_fallback_flow() -> dict | None:
    """Cari flow cadangan Gemini ("BriefME (Gemini)") via .flow_id.gemini / nama."""
    path = BASE / FLOW_ID_GEMINI
    if path.exists():
        flow_id = path.read_text().strip()
        if flow_id:
            try:
                flow = api("GET", f"/api/v1/flows/{flow_id}")
                if isinstance(flow, dict) and flow.get("id"):
                    return flow
            except SystemExit:
                pass
    name = urllib.parse.quote(FALLBACK_FLOW_NAME)
    flows = api("GET", f"/api/v1/flows/?name={name}")
    if isinstance(flows, list):
        found = next((f for f in flows if f.get("name") == FALLBACK_FLOW_NAME), None)
        if found:
            return found
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Buat/update flow Langflow BriefME")
    parser.add_argument("--engine", choices=("bob", "gemini"),
                        default=os.environ.get("ENGINE", "bob"),
                        help="bob = arsitektur utama (default); gemini = flow cadangan")
    args = parser.parse_args()
    engine = args.engine

    load_env_file(BASE / ".env")
    if not os.environ.get("LANGFLOW_API_KEY"):
        sys.exit("Set LANGFLOW_API_KEY dulu (lihat .env)")

    # MCP server tool dokumen — dipakai engine gemini (node MCP Tools) dan oleh
    # clone fallback; idempoten sehingga aman dipanggil di kedua engine.
    register_mcp_server()

    if engine == "bob":
        if not os.environ.get("BOB_API_KEY"):
            sys.exit("Set BOB_API_KEY dulu di .env (kunci API resmi Bob)")

        # Migrasi: simpan flow Gemini yang masih menempati primary sebagai
        # fallback SEBELUM data primary ditimpa dengan data bob.
        existing = find_existing_flow()
        if existing and is_gemini_style(existing) and not find_fallback_flow():
            clone = api("POST", "/api/v1/flows/",
                        {"name": FALLBACK_FLOW_NAME, "data": existing["data"],
                         "endpoint_name": FALLBACK_ENDPOINT, "description": DESC_GEMINI})
            (BASE / FLOW_ID_GEMINI).write_text(clone["id"])
            print(f"Flow fallback dicloning: {FALLBACK_FLOW_NAME} id={clone['id']} "
                  f"endpoint {FALLBACK_ENDPOINT}")

        data = build_flow_bob()
        target, name, endpoint, desc = existing, FLOW_NAME, ENDPOINT, DESC_BOB
    else:
        gemini_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not gemini_key:
            sys.exit(f"Set {GEMINI_VAR_NAME} dulu di .env (kunci Gemini untuk node Agent)")
        sync_gemini_variable(gemini_key)

        data = build_flow_gemini()
        target = find_fallback_flow()
        if target is None:
            primary = find_existing_flow()
            if primary and is_gemini_style(primary):
                target, name, endpoint, desc = primary, FLOW_NAME, ENDPOINT, DESC_GEMINI
            else:
                target = None
        if target is None:
            name, endpoint, desc = FALLBACK_FLOW_NAME, FALLBACK_ENDPOINT, DESC_GEMINI

    if target:
        flow_id = target["id"]
        api("PATCH", f"/api/v1/flows/{flow_id}",
            {"name": name, "data": data, "endpoint_name": endpoint, "description": desc})
        print(f"Flow UPDATED [{engine}]: {name} id={flow_id}")
    else:
        res = api("POST", "/api/v1/flows/",
                  {"name": name, "data": data, "endpoint_name": endpoint, "description": desc})
        flow_id = res.get("id")
        print(f"Flow CREATED [{engine}]: {name} id={flow_id}")

    if engine == "gemini":
        (BASE / FLOW_ID_GEMINI).write_text(flow_id)
    else:
        (BASE / ".flow_id").write_text(flow_id)
    print(f"engine: {engine}  endpoint: {lf_url()}/api/v1/run/{endpoint}")
    print(f"flow id disimpan di {BASE / ('.flow_id.gemini' if engine == 'gemini' else '.flow_id')}")


if __name__ == "__main__":
    main()
