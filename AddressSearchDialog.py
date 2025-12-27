# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QComboBox, QCheckBox, QMessageBox
)

from qgis.core import (
    QgsExpression, QgsFeatureRequest, QgsWkbTypes
)


class AddressSearchDialog(QDialog):
    """
    仕様（今回反映）：
    - フリーワード検索（文字列フィールド全部）を一番上
    - その下に「さらに絞り込み」（条件は1つ）
    - フリーワード と さらに絞り込み の結合は「かつ（AND）/ または（OR）」を間に表示
    - クリア：入力クリア + 選択解除 + 可能ならレイヤ選択解除も試行
    - レイヤ未選択警告（重要）
    - レイヤ情報：種類（プロバイダ/拡張子）と文字コード（取れれば）
    - 閉じるボタン
    """

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        self.setWindowTitle("属性検索（フリーワード＋さらに絞り込み）")
        self.setMinimumWidth(520)

        self._build_ui()
        self._wire_signals()

        # 初期表示更新
        self.update_layer_info()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # レイヤ情報
        self.layer_info = QLabel("")
        self.layer_info.setWordWrap(True)
        root.addWidget(self.layer_info)

        # フリーワード
        fw_row = QHBoxLayout()
        fw_row.addWidget(QLabel("フリーワード"))
        self.freeword_edit = QLineEdit()
        self.freeword_edit.setPlaceholderText("例：東京 / 施設名 / 管理番号 など（文字列フィールド全体から検索）")
        fw_row.addWidget(self.freeword_edit)
        root.addLayout(fw_row)

        # AND/OR（かつ/または）表示：条件1と2の間（= フリーワードと絞り込みの間）
        op_row = QHBoxLayout()
        op_row.addStretch(1)
        op_row.addWidget(QLabel("結合"))
        self.join_combo = QComboBox()
        self.join_combo.addItems(["かつ", "または"])  # AND / OR
        self.join_combo.setToolTip("「かつ」= 両方満たす / 「または」= どちらか満たす")
        op_row.addWidget(self.join_combo)

        self.join_help = QLabel("※「かつ」= 両方の条件に一致 / 「または」= どちらかに一致")
        self.join_help.setStyleSheet("color: #666;")
        op_row.addWidget(self.join_help)
        op_row.addStretch(1)
        root.addLayout(op_row)

        # さらに絞り込み
        group = QGroupBox("さらに絞り込み（任意）")
        g = QVBoxLayout(group)

        top = QHBoxLayout()
        self.refine_enable = QCheckBox("絞り込みを使う")
        top.addWidget(self.refine_enable)
        top.addStretch(1)
        g.addLayout(top)

        refine_row = QHBoxLayout()
        refine_row.addWidget(QLabel("絞り込み文字"))
        self.refine_edit = QLineEdit()
        self.refine_edit.setPlaceholderText("例：中央 / 1丁目 / A棟 など（文字列フィールド全体）")
        refine_row.addWidget(self.refine_edit)

        refine_row.addWidget(QLabel("一致方法"))
        self.match_combo = QComboBox()
        # 記号ではなく日本語表示
        self.match_combo.addItems(["含む", "完全一致", "前方一致"])
        self.match_combo.setToolTip("含む=部分一致 / 完全一致=一致 / 前方一致=先頭一致")
        refine_row.addWidget(self.match_combo)

        g.addLayout(refine_row)
        root.addWidget(group)

        # ボタン列
        btns = QHBoxLayout()
        self.search_btn = QPushButton("検索")
        self.clear_btn = QPushButton("クリア")
        self.close_btn = QPushButton("閉じる")
        btns.addWidget(self.search_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        root.addLayout(btns)

        # 初期状態（絞り込み無効）
        self._apply_refine_enabled(False)

    def _wire_signals(self):
        self.search_btn.clicked.connect(self.run_search)
        self.clear_btn.clicked.connect(self.clear_all)
        self.close_btn.clicked.connect(self.close)

        self.refine_enable.toggled.connect(self._apply_refine_enabled)

        # レイヤが変わったら情報更新
        self.iface.currentLayerChanged.connect(self._on_layer_changed)

    def closeEvent(self, event):
        # シグナル解除
        try:
            self.iface.currentLayerChanged.disconnect(self._on_layer_changed)
        except Exception:
            pass
        super().closeEvent(event)

    def _apply_refine_enabled(self, enabled: bool):
        self.refine_edit.setEnabled(enabled)
        self.match_combo.setEnabled(enabled)

    # ---------------- Layer Info ----------------

    def _on_layer_changed(self, _layer):
        self.update_layer_info()

    def update_layer_info(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.layer_info.setText("レイヤ：未選択（※検索前に対象レイヤをクリックして選択してください）")
            return

        # 種類（ベクタ/ラスタ）
        try:
            is_vector = layer.type() == layer.VectorLayer
        except Exception:
            is_vector = False

        layer_type = "ベクタ" if is_vector else "ラスタ/不明"
        provider = ""
        source_ext = ""
        encoding = ""

        try:
            provider = layer.dataProvider().name()
        except Exception:
            provider = "不明"

        try:
            src = layer.source() or ""
            # 拡張子っぽいもの
            import os
            _, ext = os.path.splitext(src)
            source_ext = ext.lower() if ext else ""
        except Exception:
            source_ext = ""

        # 文字コード（取れる場合だけ）
        try:
            dp = layer.dataProvider()
            enc = getattr(dp, "encoding", None)
            if callable(enc):
                encoding = enc()
            elif isinstance(enc, str):
                encoding = enc
        except Exception:
            encoding = ""

        msg = f"レイヤ：{layer.name()} / 種類：{layer_type} / プロバイダ：{provider}"
        if source_ext:
            msg += f" / 拡張子：{source_ext}"
        if encoding:
            msg += f" / 文字コード：{encoding}"
        else:
            msg += " / 文字コード：取得不可（形式により未対応）"

        self.layer_info.setText(msg)

    # ---------------- Search Logic ----------------

    def run_search(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.iface.messageBar().pushWarning("検索", "レイヤが未選択です。対象レイヤをクリックして選択してください。")
            return

        # ベクタだけ対象
        try:
            if layer.type() != layer.VectorLayer:
                self.iface.messageBar().pushWarning("検索", "ベクタレイヤを選択してください。")
                return
        except Exception:
            self.iface.messageBar().pushWarning("検索", "レイヤ種別を判定できません。ベクタレイヤを選択してください。")
            return

        freeword = self.freeword_edit.text().strip()
        refine_on = self.refine_enable.isChecked()
        refine_text = self.refine_edit.text().strip() if refine_on else ""

        if not freeword and not (refine_on and refine_text):
            self.iface.messageBar().pushWarning("検索", "フリーワード、または絞り込み文字を入力してください。")
            return

        # 文字列フィールド一覧
        str_fields = [f.name() for f in layer.fields() if f.type() == QVariant.String]
        if not str_fields:
            self.iface.messageBar().pushWarning("検索", "文字列フィールドが見つかりません。")
            return

        # フリーワード式（文字列フィールド全部 OR）
        free_expr = self._build_or_contains_expr(str_fields, freeword) if freeword else None

        # 絞り込み式（文字列フィールド全部 OR、一致方法は選択）
        refine_expr = None
        if refine_on and refine_text:
            refine_expr = self._build_refine_expr(str_fields, refine_text, self.match_combo.currentText())

        # 結合（かつ/または）
        expr_text = self._combine_expr(free_expr, refine_expr, self.join_combo.currentText())

        if not expr_text:
            self.iface.messageBar().pushWarning("検索", "検索式を作れませんでした。入力内容を確認してください。")
            return

        expr = QgsExpression(expr_text)
        if expr.hasParserError():
            self.iface.messageBar().pushCritical("検索", f"式エラー: {expr.parserErrorString()}")
            return

        req = QgsFeatureRequest(expr)

        ids = [ft.id() for ft in layer.getFeatures(req)]
        if not ids:
            layer.removeSelection()
            self.iface.messageBar().pushInfo("検索", "該当する地物がありません。")
            return

        layer.selectByIds(ids)
        self.iface.mapCanvas().zoomToSelected(layer)
        self.iface.messageBar().pushSuccess("検索", f"{len(ids)} 件ヒットしました。")

    def _escape_like(self, s: str) -> str:
        # LIKE用：' を '' に
        return s.replace("'", "''")

    def _build_or_contains_expr(self, fields, text):
        text = self._escape_like(text)
        if not text:
            return None
        # lower("a") LIKE lower('%text%') OR lower("b") LIKE ...
        parts = [f"lower(\"{name}\") LIKE lower('%{text}%')" for name in fields]
        return "(" + " OR ".join(parts) + ")" if parts else None

    def _build_refine_expr(self, fields, text, mode_label):
        text = self._escape_like(text)
        if not text:
            return None

        if mode_label == "完全一致":
            parts = [f"lower(\"{name}\") = lower('{text}')" for name in fields]
        elif mode_label == "前方一致":
            parts = [f"lower(\"{name}\") LIKE lower('{text}%')" for name in fields]
        else:  # "含む"
            parts = [f"lower(\"{name}\") LIKE lower('%{text}%')" for name in fields]

        return "(" + " OR ".join(parts) + ")" if parts else None

    def _combine_expr(self, a, b, join_label):
        # a,bは None あり得る
        if a and b:
            op = "AND" if join_label == "かつ" else "OR"
            return f"({a} {op} {b})"
        return a or b

    # ---------------- Clear ----------------

    def clear_all(self):
        # 入力クリア
        self.freeword_edit.clear()
        self.refine_enable.setChecked(False)
        self.refine_edit.clear()
        self.match_combo.setCurrentIndex(0)
        self.join_combo.setCurrentIndex(0)  # かつ

        # 選択解除
        layer = self.iface.activeLayer()
        if layer:
            try:
                layer.removeSelection()
            except Exception:
                pass

        # レイヤ選択解除（できる範囲で試行）
        # iface.setActiveLayer(None) が使える環境なら解除できる
        try:
            if hasattr(self.iface, "setActiveLayer"):
                self.iface.setActiveLayer(None)
        except Exception:
            pass

        self.update_layer_info()
        self.iface.messageBar().pushInfo("クリア", "条件をクリアしました（選択も解除しました）。")
