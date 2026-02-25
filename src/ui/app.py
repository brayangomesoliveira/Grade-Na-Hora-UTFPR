from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from src.domain.grade_model import GRID_DIAS, GRID_LINHAS, montar_grade, turma_tem_conflito, turma_uid
from src.domain.models import TurmaAberta
from src.domain.parser_horario import parse_horario_raw
from src.ui.theme import GAP, PAD, RADIUS, TOKENS, gradient_photo

logger = logging.getLogger(__name__)
SAMPLE_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "turmas_abertas.sample.json"
DIA_NOMES = {2: "Segunda", 3: "Terca", 4: "Quarta", 5: "Quinta", 6: "Sexta", 7: "Sabado"}


class GradeApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Grade na Hora UTFPR - Base")
        self.geometry("1280x760")
        self.minsize(1100, 680)
        self._tema = "dark"
        self._tokens = TOKENS[self._tema]
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel = threading.Event()
        self._turmas: list[TurmaAberta] = []
        self._selecionadas: set[str] = set()
        self._senha_memoria = ""
        self._resultado = montar_grade([])
        self._montar_ui()
        self.after(120, self._poll_queue)

    def _montar_ui(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.configure(fg_color=self._tokens["bg"])
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(1, weight=1)
        self._header()
        self._left_panel()
        self._right_panel()

    def _header(self) -> None:
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(PAD, GAP))
        self.header_canvas = tk.Canvas(box, height=60, highlightthickness=0, bd=0)
        self.header_canvas.pack(fill="x")
        self.header_title = self.header_canvas.create_text(18, 30, anchor="w", text="Grade na Hora UTFPR", fill="#FFFFFF", font=("Segoe UI", 18, "bold"))
        self.header_canvas.bind("<Configure>", self._redesenhar_header)

    def _redesenhar_header(self, _evt: object = None) -> None:
        w = max(10, self.header_canvas.winfo_width())
        self._header_img = gradient_photo(w, 60, "#0E7A7F", "#0A5F63")
        self.header_canvas.delete("bg")
        self.header_canvas.create_image(0, 0, image=self._header_img, anchor="nw", tags="bg")
        self.header_canvas.tag_raise(self.header_title)

    def _card(self, parent: ctk.CTkBaseClass, title: str) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
        outer = ctk.CTkFrame(parent, fg_color=self._tokens["surface"], border_width=1, border_color=self._tokens["border"], corner_radius=RADIUS)
        line = tk.Canvas(outer, height=3, highlightthickness=0, bd=0)
        line.pack(fill="x")
        line.bind("<Configure>", lambda _e, c=line: self._paint_line(c))
        ctk.CTkLabel(outer, text=title, text_color=self._tokens["text"], font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=PAD, pady=(10, 6))
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        return outer, body

    def _paint_line(self, canvas: tk.Canvas) -> None:
        w = max(10, canvas.winfo_width())
        img = gradient_photo(w, 3, self._tokens["line"], self._tokens["primary"])
        canvas._img = img
        canvas.delete("all")
        canvas.create_image(0, 0, image=img, anchor="nw")

    def _btn(self, parent: ctk.CTkBaseClass, text: str, primary: bool, cmd) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=text, command=cmd, corner_radius=8, fg_color=self._tokens["primary" if primary else "secondary"], hover_color=self._tokens["primary_hover" if primary else "secondary_hover"], text_color=self._tokens["text"], height=34)

    def _left_panel(self) -> None:
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(PAD, GAP), pady=(0, PAD))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        card, b = self._card(left, "Login / Entrada")
        card.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        self.ra_entry = ctk.CTkEntry(b, placeholder_text="RA (matricula)")
        self.senha_entry = ctk.CTkEntry(b, placeholder_text="Senha", show="*")
        self.prefix_var = ctk.BooleanVar(value=True)
        self.offline_var = ctk.BooleanVar(value=True)
        self.ra_entry.pack(fill="x", pady=(0, 6)); self.senha_entry.pack(fill="x", pady=(0, 6))
        ctk.CTkCheckBox(b, text="Prefixar com a", variable=self.prefix_var).pack(anchor="w")
        ctk.CTkSwitch(b, text="Modo offline", variable=self.offline_var).pack(anchor="w", pady=(6, 8))
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        self._btn(row, "Entrar", True, self._on_entrar).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.cancel_btn = self._btn(row, "Cancelar", False, self._on_cancelar); self.cancel_btn.pack(side="left", expand=True, fill="x", padx=(4, 0)); self.cancel_btn.configure(state="disabled")
        self.status_lbl = ctk.CTkLabel(b, text="Pronto (offline ligado)", anchor="w", text_color=self._tokens["muted"])
        self.status_lbl.pack(fill="x", pady=(8, 0))
        card2, b2 = self._card(left, "Turmas")
        card2.grid(row=1, column=0, sticky="nsew")
        self.filtro_var = ctk.StringVar(); self.filtro_var.trace_add("write", lambda *_: self._refresh_tree())
        ctk.CTkEntry(b2, textvariable=self.filtro_var, placeholder_text="Buscar por codigo/nome/professor").pack(fill="x", pady=(0, 8))
        self.tree = ttk.Treeview(b2, columns=("sel", "codigo", "turma", "nome", "conf"), show="headings", height=12)
        for col, txt, w in [("sel","Sel",40),("codigo","Codigo",80),("turma","Turma",60),("nome","Disciplina",220),("conf","Conflito",70)]: self.tree.heading(col, text=txt); self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_toggle_row)

    def _right_panel(self) -> None:
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(GAP, PAD), pady=(0, PAD))
        right.grid_rowconfigure(1, weight=1); right.grid_columnconfigure(0, weight=1)
        top, b = self._card(right, "Acoes / Resumo")
        top.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        bar = ctk.CTkFrame(b, fg_color="transparent"); bar.pack(fill="x")
        self._btn(bar, "Gerar (placeholder)", True, self._gerar_placeholder).pack(side="left", padx=(0,6))
        self._btn(bar, "Limpar selecao", False, self._limpar).pack(side="left", padx=(0,6))
        self._btn(bar, "Tema", False, self._trocar_tema).pack(side="left")
        self.credito_lbl = ctk.CTkLabel(b, text="Creditos usados: 0")
        self.credito_lbl.pack(anchor="w", pady=(8, 0))
        grid_card, gb = self._card(right, "Grade (base offline)")
        grid_card.grid(row=1, column=0, sticky="nsew")
        self.canvas = tk.Canvas(gb, highlightthickness=0, bd=0, background=self._tokens["surface_2"])
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_grade())
        self._draw_grade()

    def _on_entrar(self) -> None:
        self._senha_memoria = self.senha_entry.get()
        if not self.offline_var.get():
            messagebox.showinfo("Nao implementado", "Scraper real entra na proxima etapa. Use Modo offline.")
            return
        self._cancel.clear(); self.cancel_btn.configure(state="normal")
        self.status_lbl.configure(text="Carregando sample em thread...")
        threading.Thread(target=self._worker_load_sample, daemon=True).start()

    def _on_cancelar(self) -> None:
        self._cancel.set(); self.status_lbl.configure(text="Cancelamento solicitado...")

    def _worker_load_sample(self) -> None:
        try:
            dados = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
            turmas: list[TurmaAberta] = []
            for item in dados:
                if self._cancel.is_set(): return
                horarios = parse_horario_raw(item.get("horarioRaw", ""))
                turmas.append(TurmaAberta(codigo=item["codigo"], nome=item["nome"], turma=item["turma"], professor=item.get("professor"), horarioRaw=item["horarioRaw"], horarios=horarios, vagas=item.get("vagas"), prioridade=item.get("prioridade")))
            self._queue.put(("loaded", turmas))
        except Exception as exc:
            logger.exception("Falha ao carregar sample")
            self._queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "loaded":
                    self._turmas = payload; self.status_lbl.configure(text=f"{len(self._turmas)} turmas carregadas (offline)")
                    self.cancel_btn.configure(state="disabled"); self._refresh_tree(); self._recalc()
                elif kind == "error":
                    self.cancel_btn.configure(state="disabled"); self.status_lbl.configure(text=f"Erro: {payload}")
                    messagebox.showerror("Erro", f"Falha ao carregar sample: {payload}")
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _refresh_tree(self) -> None:
        q = self.filtro_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for turma in self._turmas:
            uid = turma_uid(turma)
            if q and q not in f"{turma.codigo} {turma.nome} {turma.professor or ''}".lower(): continue
            sel = "[x]" if uid in self._selecionadas else "[ ]"
            conf = "Sim" if turma_tem_conflito(turma, self._resultado) and uid in self._selecionadas else "Nao"
            self.tree.insert("", "end", iid=uid, values=(sel, turma.codigo, turma.turma, turma.nome, conf))

    def _on_toggle_row(self, _evt: object = None) -> None:
        item = self.tree.focus()
        if not item: return
        if item in self._selecionadas: self._selecionadas.remove(item)
        else: self._selecionadas.add(item)
        self._recalc()

    def _recalc(self) -> None:
        selecionadas = [t for t in self._turmas if turma_uid(t) in self._selecionadas]
        self._resultado = montar_grade(selecionadas)
        self.credito_lbl.configure(text=f"Creditos usados: {self._resultado.creditos_usados}")
        self._refresh_tree(); self._draw_grade()

    def _limpar(self) -> None:
        self._selecionadas.clear(); self._recalc()

    def _trocar_tema(self) -> None:
        self._tema = "light" if self._tema == "dark" else "dark"; self._tokens = TOKENS[self._tema]; self._montar_ui()

    def _gerar_placeholder(self) -> None:
        itens = [f"{t.codigo} - {t.turma} | {', '.join(s.codigo for s in t.horarios)}" for t in self._turmas if turma_uid(t) in self._selecionadas]
        messagebox.showinfo("Resumo (placeholder)", "\n".join(itens) if itens else "Nenhuma turma selecionada.")

    def _cor_turma(self, uid: str) -> str:
        h = hashlib.md5(uid.encode("utf-8")).hexdigest()
        return f"#{h[:6]}"

    def _draw_grade(self) -> None:
        c = self.canvas; c.delete("all")
        w, h = max(900, c.winfo_width()), max(520, c.winfo_height())
        left_w, head_h = 70, 34
        cell_w = (w - left_w - 10) / len(GRID_DIAS); cell_h = (h - head_h - 10) / len(GRID_LINHAS)
        c.configure(background=self._tokens["surface_2"])
        for i, dia in enumerate(GRID_DIAS):
            x1 = left_w + i * cell_w; x2 = x1 + cell_w
            c.create_rectangle(x1, 0, x2, head_h, fill=self._tokens["bg"], outline="")
            c.create_text((x1 + x2) / 2, head_h / 2, text=DIA_NOMES[dia], fill=self._tokens["text"], font=("Segoe UI", 10, "bold"))
        for j, linha in enumerate(GRID_LINHAS):
            y1 = head_h + j * cell_h; y2 = y1 + cell_h
            c.create_rectangle(0, y1, left_w, y2, fill=self._tokens["surface"], outline="")
            c.create_text(left_w / 2, (y1 + y2) / 2, text=linha, fill=self._tokens["text"], font=("Segoe UI", 9, "bold"))
            for i, dia in enumerate(GRID_DIAS):
                x1 = left_w + i * cell_w; x2 = x1 + cell_w
                codigo = f"{dia}{linha[0]}{linha[1:]}"
                c.create_rectangle(x1, y1, x2, y2, fill=self._tokens["grid_fill"], outline="#B9CDE5")
                turmas = self._resultado.ocupacao.get(codigo, [])
                if not turmas: continue
                uid = turma_uid(turmas[0]); conf = codigo in self._resultado.conflitos
                c.create_rectangle(x1+1, y1+1, x2-1, y2-1, fill=self._cor_turma(uid), outline=self._tokens["danger"] if conf else self._tokens["surface_2"], width=2 if conf else 1)
                txt = "/".join({t.codigo for t in turmas}) if conf else turmas[0].codigo
                c.create_text((x1+x2)/2, (y1+y2)/2, text=txt[:10], fill="#FFFFFF", font=("Segoe UI", 8, "bold"))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    ctk.set_appearance_mode("dark")
    GradeApp().mainloop()


if __name__ == "__main__":
    run()
