"""History page: every operation in the audit store, with search and exports."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.methods import method_info
from ..core.models import MethodId, OperationState
from .widgets import STATE_KIND, gb

COLUMNS = ["Date (UTC)", "Model", "Serial", "Size", "Method", "Result", "Certificate"]


def _method(value: str) -> str:
    try:
        return method_info(MethodId(value)).label
    except ValueError:
        return value


class HistoryPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.w = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search model, serial, result or certificate ID")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        self.chain = QLabel()
        top.addWidget(self.chain)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for c in range(len(COLUMNS)):
            hh.setSectionResizeMode(c, QHeaderView.Stretch if c == 1 else QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._open())
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.where = QLabel()
        self.where.setObjectName("muted")
        btns.addWidget(self.where, 1)
        for text, fn in (("View certificate", self._open), ("Export history (CSV)", self.w.export_csv)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)

    def load(self):
        ops = self.w.store.operations()
        certs = self.w.store_certificates()
        c = self.w.colors
        self.table.setRowCount(len(ops))
        for r, o in enumerate(ops):
            st = OperationState(o["state"])
            dev = o["device"]
            vals = [(o["ended_at"] or o["created_at"])[:16].replace("T", " "), dev["model"], dev["serial"],
                    gb(dev["capacity_bytes"]), _method(o["method"]),
                    o["state"] + (" (simulated)" if o["simulated"] else ""), o["op_id"]]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, o["op_id"])
                if col == 5:
                    it.setForeground(QColor(c.get(STATE_KIND.get(st, "accent"), c["text"])))
                if col == 6 and o["op_id"] not in certs:
                    it.setForeground(QColor(c["muted"]))
                self.table.setItem(r, col, it)
        ok, msg = self.w.store.verify_chain()
        self.chain.setText("Audit chain intact" if ok else "AUDIT CHAIN BROKEN: " + msg)
        self.chain.setStyleSheet(f"color: {c['ok' if ok else 'bad']}; font-weight: 700;")
        self.where.setText(f"Reports folder: {self.w.reports_dir}")
        self._filter(self.search.text())

    def _filter(self, text: str):
        t = text.lower().strip()
        for r in range(self.table.rowCount()):
            row = " ".join(self.table.item(r, c).text().lower() for c in range(self.table.columnCount()))
            self.table.setRowHidden(r, bool(t) and t not in row)

    def _open(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.w.show_certificate_for_op(self.table.item(rows[0].row(), 0).data(Qt.UserRole))
