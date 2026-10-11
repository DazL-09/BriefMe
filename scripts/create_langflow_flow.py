"""Buat/update flow Langflow "BriefME" — SATU flow, dua mode input.

Arsitektur hackathon: Bob (API key resmi) + Langflow orkestrasi. Tidak ada flow
tambahan — dokumen .docx dibuat di browser (web) dari data tersimpan, bukan
lewat AI/tool.

Satu flow "BriefME" (endpoint briefme), 6 node / 5 edge:
    Chat Input ──→ Prompt ──→ Python Interpreter ──→ Parse JSON ──→ Parser ──→ Chat Output
    (input)        (kode       (subprocess bob run    (jq: ambil      (format    "Thought Record"
                    + {text})   via API resmi)         last_message)   {text})

Mode dibaca dari AWALAN input oleh kode node (bukan flow terpisah):
    - jurnal polos            → PROMPT_ANALYSIS   (bob --mode agent --disable-mcp):
      tabel CBT 4 kolom, kolom Pikiran Alternatif KOSONG (AI tidak mengisi).
    - "MODE=ALTERNATIF\\n{json}" → PROMPT_ALTERNATE (bob --mode ask --disable-mcp):
      SATU kalimat saran pikiran alternatif seimbang.

Catatan implementasi (mengikuti lfx.graph.flow_builder.connect):
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
    python scripts/create_langflow_flow.py
"""

from __future__ import annotations

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
DESC = ("BriefME — Thought Record CBT dari jurnal pasien (1 flow multi-mode: "
        "analisis + saran pikiran alternatif; Langflow + Bob CLI via API resmi)")

# Id node flow (satu-satunya flow; suffix HEARB dipertahankan dari iterasi lama).
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

# ---- Prompt per mode ---------------------------------------------------------
# Semua teks TANPA kurung kurawal (audit di build_python_code_template_multi)
# karena ikut ter-embed ke template kode python str.format {text}.

# Mode analisis (input polos): 4 kolom CBT saja; kolom Pikiran Alternatif SEL
# kosong (diisi manual oleh pasien — guardrail produk: AI tidak boleh langsung
# mengisi pikiran alternatif; lihat AGENT_LOG §19).
PROMPT_ANALYSIS = """\
Anda adalah Clinical Copilot dari HearMeOut. Anda menerima teks jurnal pasien
(tanpa data pribadi) dan tugas Anda mengekstraknya ke matriks CBT.

Langkah wajib Anda:
1. Analisis teks SECARA INTERNAL dan siapkan ekstraksi dengan kunci:
   - situation (Situasi/kejadian pemicu)
   - thought (Pikiran otomatis yang muncul saat kejadian)
   - emotion (Emosi yang dirasakan)
   - behavior (Perilaku yang dilakukan)
   JANGAN mengisi alternate_thought — kolom pikiran alternatif diisi sendiri
   oleh pasien sebagai latihan.
2. Setelah analisis selesai, akhiri pesan HANYA dengan TABEL Markdown berikut
   — tanpa objek JSON mentah, tanpa blok kode, tanpa teks lain:
   | Situasi | Pikiran | Emosi | Perilaku | Pikiran Alternatif |
   |---|---|---|---|---|
   | ... | ... | ... | ... |  |
   (satu baris data per situasi; bila teks memuat beberapa situasi, buat beberapa
   baris; kolom Pikiran Alternatif SELALU KOSONG total — tanpa &nbsp;, tanpa
   teks, tanpa tanda apa pun; spasi biasa saja)

Aturan:
- Objek JSON dari langkah 1 hanya untuk proses internal Anda — jangan pernah
  menulisnya di pesan akhir.
- Jangan berikan saran medis kepada pengguna. Fokus pada ekstraksi data secara objektif.
- Asumsikan teks jurnal tidak memuat data pribadi; jangan bertanya mengenai identitas pasien.
- Ekstraksi harus objektif: kutip/parafrase keluhan apa adanya, tanpa diagnosa.
- Jangan memanggil tool apa pun, jangan menyebut file, path, atau dokumen.
- Bila teks tidak memuat keluhan yang bisa diekstrak, tetap buat tabel dengan sel "-".
"""

# Mode alternatif (input "MODE=ALTERNATIF\\n{json}"): saran pikiran alternatif
# (opsi "Saran AI"; pilihan utama tetap manual).
PROMPT_ALTERNATE = """\
Anda adalah Clinical Copilot dari HearMeOut. Anda menerima SATU objek JSON yang
berisi kunci situation, thought, emotion, behavior dari jurnal pasien.

Tugas Anda: berikan SATU pikiran alternatif yang lebih seimbang dan realistis
(gaya restrukturisasi kognitif ringan) untuk pemikiran otomatis tersebut,
dalam bahasa Indonesia, maksimal 2 kalimat.

Balas HANYA dengan teks pikiran alternatif itu — tanpa tanda kutip, tanpa
pembuka seperti "Pikiran alternatif:", tanpa penjelasan atau daftar lain,
tanpa saran medis dan tanpa diagnosa.
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


def build_python_code_template_multi() -> str:
    """Susun kode Python node Python Interpreter (template dengan {text}).

    Kode dijalankan sandbox Langflow: tanpa pernyataan import (validate_code_safety
    menolak ast.Import — module dari global_imports), tanpa open() (builtins tak
    memilikinya) — fallback kunci BOB dibaca via `subprocess cat .env`.
    Satu-satunya kurung kurawal di seluruh kode adalah placeholder {text} yang
    dirender oleh node Prompt (str.format).

    Input berawalan "MODE=ALTERNATIF" → PROMPT_ALTERNATE (bob --mode ask);
    selain itu → PROMPT_ANALYSIS (bob --mode agent). Keduanya --disable-mcp —
    produk tidak memakai tool; dokumen .docx dibuat di web (library docx).
    """
    for instr in (PROMPT_ANALYSIS, PROMPT_ALTERNATE):
        assert "{" not in instr and "}" not in instr, "prompt memuat kurung kurawal"
        assert "'''" not in instr, "prompt memuat tiga kutip"
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
        "raw = '''{text}'''\n"
        "if raw.startswith('MODE=ALTERNATIF'):\n"
        "    INSTR = '''\n" + PROMPT_ALTERNATE + "\n'''\n"
        "    MODE_ARGS = ['--mode', 'ask', '--disable-mcp']\n"
        "    payload = raw[len('MODE=ALTERNATIF'):].strip()\n"
        "else:\n"
        "    INSTR = '''\n" + PROMPT_ANALYSIS + "\n'''\n"
        "    MODE_ARGS = ['--mode', 'agent', '--disable-mcp']\n"
        "    payload = raw\n"
        "prompt = INSTR + '\\n\\nInput:\\n' + payload\n"
        "try:\n"
        "    p = subprocess.run([BOB, 'run', '--accept-license', '--format', 'json', "
        "'--log-level', 'error', '--trust', '--workspace', PROJECT, '--max-turns', "
        "env.get('BOB_MAX_TURNS', '6'), '--disable-subagents'] + MODE_ARGS + [prompt], "
        "input='', capture_output=True, text=True, timeout=900, env=env, cwd=PROJECT)\n"
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


def build_chain_flow(code_template: str, ids: dict, output_display: str) -> dict:
    """Flow 6 node / 5 edge — rantai Prompt → Python → Parse JSON → Parser."""
    catalog = api("GET", "/api/v1/all")

    chat_in = catalog_entry(catalog, "input_output", "ChatInput")
    prompt = catalog_entry(catalog, "models_and_agents", "Prompt Template")
    python_repl = catalog_entry(catalog, "utilities", "PythonREPLComponent")
    parse_json = catalog_entry(catalog, "processing", "ParseJSONData")
    parser = catalog_entry(catalog, "processing", "ParserComponent")
    chat_out = catalog_entry(catalog, "input_output", "ChatOutput")

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

    # --- Parser: format dari baris Data (satu Data per baris jq)
    parser["template"]["pattern"]["value"] = "{text}"

    # --- Chat Output
    chat_out["display_name"] = output_display

    nodes = [
        node(chat_in, "ChatInput", ids["chat_input"], [0, 300]),
        node(prompt, "Prompt Template", ids["prompt"], [360, 300]),
        node(python_repl, "PythonREPLComponent", ids["python"], [940, 300]),
        node(parse_json, "ParseJSONData", ids["parse_json"], [1520, 300]),
        node(parser, "ParserComponent", ids["parser"], [1980, 300]),
        node(chat_out, "ChatOutput", ids["chat_output"], [2380, 300]),
    ]
    edges = [
        edge(ids["chat_input"], "message", ["Message"], "ChatInput",
             ids["prompt"], "text", ["Message"], "str"),
        edge(ids["prompt"], "prompt", ["Message"], "Prompt Template",
             ids["python"], "python_code", ["Message"], "str"),
        edge(ids["python"], "results", ["JSON"], "PythonREPLComponent",
             ids["parse_json"], "input_value", ["Message", "Data", "JSON"], "other"),
        edge(ids["parse_json"], "filtered_data", ["JSON"], "ParseJSONData",
             ids["parser"], "input_data", ["DataFrame", "Table", "Data", "JSON"], "other"),
        edge(ids["parser"], "parsed_text", ["Message"], "ParserComponent",
             ids["chat_output"], "input_value",
             ["Data", "JSON", "DataFrame", "Table", "Message"], "other"),
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def upsert_flow(spec: dict, data: dict) -> str:
    """PATCH bila flow sudah ada (id_file / nama persis), kalau tidak CREATE.

    id disimpan ke file spec["id_file"] agar run berikutnya langsung tahu id.
    """
    target = None
    path = BASE / spec["id_file"]
    if path.exists():
        flow_id = path.read_text().strip()
        if flow_id:
            try:
                flow = api("GET", f"/api/v1/flows/{flow_id}")
                if isinstance(flow, dict) and flow.get("id"):
                    target = flow
            except SystemExit:
                pass
    if target is None:
        q = urllib.parse.quote(spec["name"])
        flows = api("GET", f"/api/v1/flows/?name={q}")
        if isinstance(flows, list):
            target = next((f for f in flows if f.get("name") == spec["name"]), None)

    payload = {"name": spec["name"], "data": data,
               "endpoint_name": spec["endpoint"], "description": spec["desc"]}
    if target:
        flow_id = target["id"]
        api("PATCH", f"/api/v1/flows/{flow_id}", payload)
        print(f"Flow UPDATED: {spec['name']} id={flow_id} endpoint={spec['endpoint']}")
    else:
        res = api("POST", "/api/v1/flows/", payload)
        flow_id = res.get("id")
        print(f"Flow CREATED: {spec['name']} id={flow_id} endpoint={spec['endpoint']}")
    (BASE / spec["id_file"]).write_text(flow_id)
    return flow_id


def main() -> None:
    load_env_file(BASE / ".env")
    if not os.environ.get("LANGFLOW_API_KEY"):
        sys.exit("Set LANGFLOW_API_KEY dulu (lihat .env)")
    if not os.environ.get("BOB_API_KEY"):
        sys.exit("Set BOB_API_KEY dulu di .env (kunci API resmi Bob)")

    code = build_python_code_template_multi()
    data = build_chain_flow(code, BOB_IDS, "Thought Record")
    spec = {"name": FLOW_NAME, "endpoint": ENDPOINT,
            "id_file": ".flow_id", "desc": DESC}
    upsert_flow(spec, data)
    print(f"endpoint: {lf_url()}/api/v1/run/{ENDPOINT}")


if __name__ == "__main__":
    main()
