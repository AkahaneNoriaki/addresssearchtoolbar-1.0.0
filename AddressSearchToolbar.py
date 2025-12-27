# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Any

from qgis.PyQt.QtCore import Qt, QSettings, QTimer, QUrl, QModelIndex
from qgis.PyQt.QtGui import QIcon, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from qgis.core import (
    QgsExpression,
    QgsFeatureRequest,
    QgsProject,
    QgsVectorLayer,
    QgsMapLayer,
    QgsWkbTypes,
)

# =====================================
# Utility
# =====================================

def _escape_like(s: str) -> str:
    # LIKE に入れる前提。% _ \ をエスケープ（安全寄り）
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _provider_label(layer: QgsMapLayer) -> str:
    try:
        return layer.providerType() if layer else "-"
    except Exception:
        return "-"

def _layer_type_label(layer: QgsMapLayer) -> str:
    if not layer:
        return "未選択"
    if isinstance(layer, QgsVectorLayer):
        g = QgsWkbTypes.displayString(layer.wkbType())
        return f"ベクタ（{g}）"
    return "ベクタ以外"

def _encoding_label(layer: QgsMapLayer) -> str:
    if not layer or not isinstance(layer, QgsVectorLayer):
        return "-"
    try:
        enc = layer.dataProvider().encoding()
        return enc if enc else "-"
    except Exception:
        return "-"

def _is_number(s: str) -> bool:
    return re.fullmatch(r"[+-]?\d+(\.\d+)?", s or "") is not None

def _open_path_cross_platform(path: str) -> None:
    try:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception:
        pass


# =====================================
# Dialog
# =====================================

class AddressSearchDialog(QDialog):
    """
    UI（要望反映）
    - フリーワード（上）
    - 次に「機能拡張（折りたたみ）」：絞り込み + AND/OR（表示は かつ/または）
      * 起動時は必ず折りたたみ
      * 絞り込みチェックは外した状態
    - 条件クリア：レイヤ選択解除は必ず、フォルダは保持
      * さらに：QGIS本体のアクティブレイヤも解除（可能な範囲で）
      * さらに：直前レイヤの選択地物も解除
    - 結果テーブル（属性表示）維持
      * すべての列を表示
      * ダブルクリックで地物へズーム
    - ファイル検索（フォルダ指定保持）
    - 最小化ボタン：タイトルバー
    """

    SETTINGS_GROUP = "AddressSearchToolbarFieldSelect"

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        # 直前に選択していたレイヤ（クリア時の removeSelection 用）
        self._last_layer_id: Optional[str] = None

        self.setWindowTitle("属性検索（フリーワード＋機能拡張）")
        self.setMinimumWidth(820)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint)

        self._build_ui()

        # signals
        self.btn_search.clicked.connect(self.run_search)
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_close.clicked.connect(self.close)

        self.btn_pick_folder.clicked.connect(self.pick_folder)
        self.btn_file_search.clicked.connect(self.run_file_search)
        self.btn_open_folder.clicked.connect(self.open_folder)

        self.combo_layer.currentIndexChanged.connect(self.on_layer_changed)

        self.combo_logic.currentIndexChanged.connect(self._save_state)
        self.chk_use_filter.stateChanged.connect(self._save_state)
        self.combo_field.currentIndexChanged.connect(self._save_state)
        self.combo_op.currentIndexChanged.connect(self._save_state)
        self.edit_filter.textChanged.connect(self._save_state)
        self.edit_free.textChanged.connect(self._save_state)
        self.edit_folder.textChanged.connect(self._save_state)
        self.edit_ext.textChanged.connect(self._save_state)

        self.toggle_adv.clicked.connect(self._toggle_advanced)

        self.tbl_results.cellDoubleClicked.connect(self._zoom_from_result_row)
        self.list_files.itemDoubleClicked.connect(self._open_selected_file)

        QgsProject.instance().layersAdded.connect(lambda *_: self.refresh_layers())
        QgsProject.instance().layersRemoved.connect(lambda *_: self.refresh_layers())

        # init
        self.refresh_layers()
        self._restore_state()

        QTimer.singleShot(0, self.on_layer_changed)

        # 起動時は必ず折りたたみ・チェックOFF（要望）
        self._set_advanced_collapsed(True)
        self.chk_use_filter.setChecked(False)

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ---- layer box ----
        box_layer = QGroupBox("対象レイヤ")
        gl = QGridLayout(box_layer)

        self.combo_layer = QComboBox()
        self.combo_layer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.btn_unselect_layer = QPushButton("レイヤ選択解除")
        self.btn_unselect_layer.clicked.connect(self.unselect_layer)

        gl.addWidget(QLabel("レイヤ:"), 0, 0)
        gl.addWidget(self.combo_layer, 0, 1)
        gl.addWidget(self.btn_unselect_layer, 0, 2)

        self.lbl_layer_info = QLabel("レイヤ情報：未選択（検索時に警告）")
        self.lbl_layer_info.setWordWrap(True)
        gl.addWidget(self.lbl_layer_info, 1, 0, 1, 3)

        root.addWidget(box_layer)

        # ---- freeword (top) ----
        box_free = QGroupBox("フリーワード検索（文字列フィールドすべて）")
        v = QVBoxLayout(box_free)
        self.edit_free = QLineEdit()
        self.edit_free.setPlaceholderText("例）中央 / 赤坂 / 〇〇マンション など")
        v.addWidget(self.edit_free)
        root.addWidget(box_free)

        # ---- advanced header (toggle button on LEFT) ----
        row_adv_header = QHBoxLayout()
        self.toggle_adv = QToolButton()
        self.toggle_adv.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_adv.setArrowType(Qt.RightArrow)
        self.toggle_adv.setText("機能拡張")
        self.toggle_adv.setCheckable(True)
        self.toggle_adv.setChecked(False)
        row_adv_header.addWidget(self.toggle_adv, 0, Qt.AlignLeft)

        self.lbl_logic_help = QLabel(
            "（説明）かつ＝両方の条件を満たす / または＝どちらか一方でも満たす"
        )
        self.lbl_logic_help.setStyleSheet("color:#555;")
        self.lbl_logic_help.setWordWrap(True)
        row_adv_header.addWidget(self.lbl_logic_help, 1)

        root.addLayout(row_adv_header)

        # ---- advanced group (collapsible) ----
        self.advanced_group = QGroupBox("")
        gl2 = QGridLayout(self.advanced_group)

        gl2.addWidget(QLabel("フリーワード と 機能拡張 の関係："), 0, 0)
        self.combo_logic = QComboBox()
        self.combo_logic.addItems(["かつ（AND）", "または（OR）"])
        gl2.addWidget(self.combo_logic, 0, 1, 1, 2)

        self.chk_use_filter = QCheckBox("さらに絞り込みを使う（属性条件）")
        self.chk_use_filter.setChecked(False)
        gl2.addWidget(self.chk_use_filter, 1, 0, 1, 3)

        gl2.addWidget(QLabel("フィールド:"), 2, 0)
        self.combo_field = QComboBox()
        gl2.addWidget(self.combo_field, 2, 1, 1, 2)

        gl2.addWidget(QLabel("条件:"), 3, 0)
        self.combo_op = QComboBox()
        self.combo_op.addItems([
            "等しい（=）",
            "より大きい（>）",
            "より小さい（<）",
            "含む（contains）",
        ])
        gl2.addWidget(self.combo_op, 3, 1)

        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("例）100 / ABC / 札幌 など（数値もOK）")
        gl2.addWidget(self.edit_filter, 3, 2)

        root.addWidget(self.advanced_group)

        # ---- buttons ----
        row_btn = QHBoxLayout()
        self.btn_search = QPushButton("検索")
        self.btn_clear = QPushButton("条件クリア")
        self.btn_close = QPushButton("閉じる")
        row_btn.addWidget(self.btn_search)
        row_btn.addWidget(self.btn_clear)
        row_btn.addStretch(1)
        row_btn.addWidget(self.btn_close)
        root.addLayout(row_btn)

        # ---- results table ----
        box_res = QGroupBox("検索結果（属性テーブル）※ ダブルクリックで地物へズーム")
        vr = QVBoxLayout(box_res)

        self.tbl_results = QTableWidget()
        self.tbl_results.setColumnCount(0)
        self.tbl_results.setRowCount(0)
        self.tbl_results.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_results.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl_results.horizontalHeader().setStretchLastSection(True)
        self.tbl_results.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        vr.addWidget(self.tbl_results)
        root.addWidget(box_res)

        # ---- file search ----
        box_files = QGroupBox("ファイル検索（指定フォルダ内：PDF/画像/Excel/Word など）")
        vf = QVBoxLayout(box_files)

        row_folder = QHBoxLayout()
        self.edit_folder = QLineEdit()
        self.edit_folder.setPlaceholderText("検索対象フォルダ（例：共有フォルダ / USBメモリ）")
        self.btn_pick_folder = QPushButton("フォルダ選択")
        row_folder.addWidget(self.edit_folder, 1)
        row_folder.addWidget(self.btn_pick_folder)
        vf.addLayout(row_folder)

        row_ext = QHBoxLayout()
        row_ext.addWidget(QLabel("対象拡張子（カンマ区切り）:"))
        self.edit_ext = QLineEdit()
        self.edit_ext.setText("pdf, png, jpg, jpeg, tif, tiff, xlsx, xls, docx, doc, pptx, ppt")
        row_ext.addWidget(self.edit_ext, 1)
        vf.addLayout(row_ext)

        row_fs = QHBoxLayout()
        self.btn_file_search = QPushButton("ファイル検索")
        self.lbl_fs_note = QLabel("※ 日本語ファイル名OK（ファイル名の部分一致）")
        self.lbl_fs_note.setStyleSheet("color:#555;")
        row_fs.addWidget(self.btn_file_search)
        row_fs.addWidget(self.lbl_fs_note, 1)
        vf.addLayout(row_fs)

        self.list_files = QListWidget()
        vf.addWidget(self.list_files)

        row_open = QHBoxLayout()
        self.btn_open_folder = QPushButton("フォルダを開く")
        row_open.addWidget(self.btn_open_folder)
        row_open.addStretch(1)
        vf.addLayout(row_open)

        root.addWidget(box_files)

    def _toggle_advanced(self):
        expanded = self.toggle_adv.isChecked()
        self._set_advanced_collapsed(not expanded)
        self._save_state()

    def _set_advanced_collapsed(self, collapsed: bool):
        self.advanced_group.setVisible(not collapsed)
        self.toggle_adv.setArrowType(Qt.RightArrow if collapsed else Qt.DownArrow)
        self.toggle_adv.setChecked(not collapsed)

    # ---------------- Settings ----------------

    def _settings(self) -> QSettings:
        return QSettings()

    def _save_state(self):
        s = self._settings()
        s.beginGroup(self.SETTINGS_GROUP)
        s.setValue("free", self.edit_free.text())
        s.setValue("adv_expanded", self.toggle_adv.isChecked())
        s.setValue("logic", self.combo_logic.currentIndex())
        s.setValue("use_filter", self.chk_use_filter.isChecked())
        s.setValue("field", self.combo_field.currentText())
        s.setValue("op", self.combo_op.currentIndex())
        s.setValue("filter_text", self.edit_filter.text())
        s.setValue("folder", self.edit_folder.text())
        s.setValue("ext", self.edit_ext.text())
        s.setValue("layer_name", self.combo_layer.currentText())
        s.endGroup()

    def _restore_state(self):
        s = self._settings()
        s.beginGroup(self.SETTINGS_GROUP)

        self.edit_free.setText(s.value("free", "", type=str))
        adv_expanded = s.value("adv_expanded", False, type=bool)
        self._set_advanced_collapsed(not adv_expanded)

        self.combo_logic.setCurrentIndex(s.value("logic", 0, type=int))
        self.chk_use_filter.setChecked(s.value("use_filter", False, type=bool))
        self.combo_op.setCurrentIndex(s.value("op", 0, type=int))
        self.edit_filter.setText(s.value("filter_text", "", type=str))

        self.edit_folder.setText(s.value("folder", "", type=str))
        self.edit_ext.setText(s.value("ext", self.edit_ext.text(), type=str))

        layer_name = s.value("layer_name", "", type=str)
        if layer_name:
            idx = self.combo_layer.findText(layer_name)
            if idx >= 0:
                self.combo_layer.setCurrentIndex(idx)

        field_name = s.value("field", "", type=str)
        if field_name:
            idxf = self.combo_field.findText(field_name)
            if idxf >= 0:
                self.combo_field.setCurrentIndex(idxf)

        s.endGroup()

    # ---------------- Layer ----------------

    def refresh_layers(self):
        current = self.combo_layer.currentText()
        self.combo_layer.blockSignals(True)

        self.combo_layer.clear()
        self.combo_layer.addItem("")  # 未選択

        layers = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsVectorLayer)
        ]
        layers.sort(key=lambda x: x.name().lower())

        for lyr in layers:
            self.combo_layer.addItem(lyr.name())

        if current:
            idx = self.combo_layer.findText(current)
            if idx >= 0:
                self.combo_layer.setCurrentIndex(idx)

        self.combo_layer.blockSignals(False)
        self.on_layer_changed()

    def _get_selected_layer(self) -> Optional[QgsVectorLayer]:
        name = self.combo_layer.currentText().strip()
        if not name:
            return None
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == name:
                return lyr
        return None

    def _get_layer_by_id(self, layer_id: Optional[str]) -> Optional[QgsVectorLayer]:
        if not layer_id:
            return None
        lyr = QgsProject.instance().mapLayer(layer_id)
        return lyr if isinstance(lyr, QgsVectorLayer) else None

    def _clear_qgis_active_layer(self):
        """
        QGIS本体の「アクティブレイヤ」を外す（可能な範囲で）
        Windows/Mac 共通で安全に try する。
        """
        try:
            # QGIS では layerTreeView があることが多い
            view = self.iface.layerTreeView()
            if view is not None:
                view.setCurrentIndex(QModelIndex())
        except Exception:
            pass
        try:
            # iface によっては setActiveLayer(None) が通る
            self.iface.setActiveLayer(None)
        except Exception:
            pass

    def on_layer_changed(self):
        layer = self._get_selected_layer()

        # 直前レイヤIDを更新（クリアで選択解除するため）
        if layer is not None:
            self._last_layer_id = layer.id()

        if layer is None:
            self.combo_field.clear()
            self.lbl_layer_info.setText("レイヤ情報：未選択（検索時に警告）")
            self._save_state()
            return

        # フィールド一覧
        self.combo_field.blockSignals(True)
        keep = self.combo_field.currentText()
        self.combo_field.clear()
        for f in layer.fields():
            self.combo_field.addItem(f.name())
        if keep:
            idx = self.combo_field.findText(keep)
            if idx >= 0:
                self.combo_field.setCurrentIndex(idx)
        self.combo_field.blockSignals(False)

        info = (
            f"レイヤ情報：{layer.name()} / 種類: {_layer_type_label(layer)} / "
            f"プロバイダ: {_provider_label(layer)} / 文字コード: {_encoding_label(layer)}"
        )
        self.lbl_layer_info.setText(info)
        self._save_state()

    def unselect_layer(self):
        """
        「レイヤ選択解除」
        - 直前レイヤの選択地物を解除
        - プラグインのコンボを未選択へ
        - QGIS本体のアクティブレイヤも解除（可能な範囲で）
        """
        # 直前レイヤの選択解除
        prev = self._get_layer_by_id(self._last_layer_id)
        if prev is not None:
            try:
                prev.removeSelection()
            except Exception:
                pass

        # コンボを未選択へ（シグナルを確実に通す）
        self.combo_layer.blockSignals(True)
        self.combo_layer.setCurrentIndex(0)
        self.combo_layer.blockSignals(False)

        # 表示更新＆設定保存
        self.on_layer_changed()

        # QGISのアクティブレイヤも外す
        self._clear_qgis_active_layer()

    # ---------------- Clear ----------------

    def clear_all(self):
        """
        要望：
        - 条件クリアでレイヤ選択も解除（確実に）
        - フォルダ指定は保持
        """
        self.edit_free.clear()

        # 機能拡張は折りたたみ＆チェックOFF
        self._set_advanced_collapsed(True)
        self.combo_logic.setCurrentIndex(0)
        self.chk_use_filter.setChecked(False)
        self.combo_op.setCurrentIndex(0)
        self.edit_filter.clear()

        # レイヤ選択解除（確実に）
        self.unselect_layer()

        # 結果・ファイル結果はクリア（フォルダは保持）
        self.tbl_results.setRowCount(0)
        self.tbl_results.setColumnCount(0)
        self.list_files.clear()

        self._save_state()
        self.iface.messageBar().pushInfo(
            "条件クリア",
            "検索条件をクリアしました（レイヤ選択も解除、フォルダは保持）"
        )

    # ---------------- Search (attribute) ----------------

    def _warn_no_layer(self):
        self.iface.messageBar().pushWarning("警告", "レイヤが選択されていません")

    def run_search(self):
        layer = self._get_selected_layer()
        if layer is None:
            self._warn_no_layer()
            return

        free = self.edit_free.text().strip()
        logic_and = (self.combo_logic.currentIndex() == 0)

        use_filter = self.chk_use_filter.isChecked()
        field = self.combo_field.currentText().strip()
        op_idx = self.combo_op.currentIndex()
        filt = self.edit_filter.text().strip()

        if not free and not (use_filter and filt):
            self.iface.messageBar().pushWarning("検索", "フリーワードまたは機能拡張条件を入力してください")
            return

        # フリーワード：文字列フィールドすべて
        expr_free = None
        if free:
            free_esc = _escape_like(free)
            parts = []
            for f in layer.fields():
                tn = (f.typeName() or "").lower()
                if tn in ("string", "text", "varchar", "char", "nchar", "nvarchar"):
                    parts.append(f'lower("{f.name()}") LIKE lower(\'%{free_esc}%\')')
            if parts:
                expr_free = "(" + " OR ".join(parts) + ")"
            else:
                self.iface.messageBar().pushWarning("検索", "文字列フィールドが見つかりません")
                return

        # 機能拡張（さらに絞り込み）
        expr_filter = None
        if use_filter and filt and field:
            field_q = f"\"{field}\""
            if op_idx == 0:  # =
                if _is_number(filt):
                    expr_filter = f"({field_q} = {filt})"
                else:
                    safe = filt.replace("'", "''")
                    expr_filter = f"({field_q} = '{safe}')"
            elif op_idx == 1:  # >
                if not _is_number(filt):
                    self.iface.messageBar().pushWarning("検索", "「より大きい」は数値を入力してください")
                    return
                expr_filter = f"({field_q} > {filt})"
            elif op_idx == 2:  # <
                if not _is_number(filt):
                    self.iface.messageBar().pushWarning("検索", "「より小さい」は数値を入力してください")
                    return
                expr_filter = f"({field_q} < {filt})"
            else:  # contains
                filt_esc = _escape_like(filt)
                expr_filter = f"(lower({field_q}) LIKE lower('%{filt_esc}%'))"

        # 結合
        if expr_free and expr_filter:
            joiner = " AND " if logic_and else " OR "
            final_expr = f"({expr_free}){joiner}({expr_filter})"
        else:
            final_expr = expr_free or expr_filter

        if not final_expr:
            self.iface.messageBar().pushWarning("検索", "有効な検索条件が作れませんでした")
            return

        qexpr = QgsExpression(final_expr)
        if qexpr.hasParserError():
            self.iface.messageBar().pushCritical("検索", f"式エラー: {qexpr.parserErrorString()}")
            return

        try:
            req = QgsFeatureRequest(qexpr)
            feats = list(layer.getFeatures(req))
            ids = [f.id() for f in feats]

            if not ids:
                layer.removeSelection()
                self._fill_results_table(layer, [])
                self.iface.messageBar().pushInfo("検索", "該当する地物がありません")
                return

            layer.selectByIds(ids)
            self.iface.mapCanvas().zoomToSelected(layer)
            self._fill_results_table(layer, feats)

            self.iface.messageBar().pushSuccess("検索", f"{len(ids)} 件ヒットしました")
        except Exception as e:
            self.iface.messageBar().pushCritical("検索", f"検索中にエラー: {e}")

        self._save_state()

    def _fill_results_table(self, layer: QgsVectorLayer, feats: List[Any]):
        """
        属性テーブル表示：要望により「全フィールド表示」
        先頭列に FID を入れて、ダブルクリックズームに使用。
        """
        fields = layer.fields()
        field_names = [f.name() for f in fields]

        headers = ["FID"] + field_names
        self.tbl_results.setColumnCount(len(headers))
        self.tbl_results.setHorizontalHeaderLabels(headers)

        self.tbl_results.setRowCount(len(feats))
        for r, ft in enumerate(feats):
            fid_item = QTableWidgetItem(str(ft.id()))
            fid_item.setData(Qt.UserRole, ft.id())
            fid_item.setFlags(fid_item.flags() ^ Qt.ItemIsEditable)
            self.tbl_results.setItem(r, 0, fid_item)

            attrs = ft.attributes()
            for c, name in enumerate(field_names, start=1):
                idx = fields.indexOf(name)
                val = attrs[idx] if idx >= 0 else None
                txt = "" if val is None else str(val)
                it = QTableWidgetItem(txt)
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                self.tbl_results.setItem(r, c, it)

        self.tbl_results.resizeColumnsToContents()

    def _zoom_from_result_row(self, row: int, col: int):
        layer = self._get_selected_layer()
        if layer is None:
            self._warn_no_layer()
            return
        fid_item = self.tbl_results.item(row, 0)
        if not fid_item:
            return
        fid = fid_item.data(Qt.UserRole)
        if fid is None:
            return

        try:
            layer.selectByIds([int(fid)])
            self.iface.mapCanvas().zoomToSelected(layer)
        except Exception:
            pass

    # ---------------- File search ----------------

    def pick_folder(self):
        base = self.edit_folder.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "検索対象フォルダを選択", base)
        if folder:
            self.edit_folder.setText(folder)
            self._save_state()

    def _parse_exts(self) -> List[str]:
        raw = self.edit_ext.text()
        parts = [p.strip().lower().lstrip(".") for p in raw.split(",") if p.strip()]
        out = []
        for p in parts:
            if p and p not in out:
                out.append(p)
        return out

    def run_file_search(self):
        base = self.edit_folder.text().strip()
        if not base:
            QMessageBox.warning(self, "ファイル検索", "検索対象フォルダを指定してください（USBメモリも可）")
            return
        if not os.path.isdir(base):
            QMessageBox.warning(self, "ファイル検索", "指定フォルダが存在しません")
            return

        kw1 = self.edit_free.text().strip()
        kw2 = self.edit_filter.text().strip() if self.chk_use_filter.isChecked() else ""

        if not kw1 and not kw2:
            QMessageBox.information(self, "ファイル検索", "フリーワード（または絞り込み値）を入力してください")
            return

        tokens = []
        if kw1:
            tokens.append(kw1)
        if kw2:
            tokens.append(kw2)

        logic_and = (self.combo_logic.currentIndex() == 0)
        exts = self._parse_exts()

        self.list_files.clear()
        hits: List[Path] = []

        base_path = Path(base)
        try:
            for p in base_path.rglob("*"):
                if not p.is_file():
                    continue
                if exts and p.suffix.lower().lstrip(".") not in exts:
                    continue

                name = p.name
                if len(tokens) == 1:
                    if tokens[0] in name:
                        hits.append(p)
                else:
                    if logic_and:
                        if all(t in name for t in tokens):
                            hits.append(p)
                    else:
                        if any(t in name for t in tokens):
                            hits.append(p)
        except Exception as e:
            QMessageBox.critical(self, "ファイル検索", f"検索中にエラー: {e}")
            return

        for p in hits[:500]:
            self.list_files.addItem(QListWidgetItem(str(p)))

        QMessageBox.information(self, "ファイル検索", f"{len(hits)} 件見つかりました（最大500件表示）")
        self._save_state()

    def _open_selected_file(self, item: QListWidgetItem):
        path = item.text()
        if path and os.path.exists(path):
            _open_path_cross_platform(path)

    def open_folder(self):
        base = self.edit_folder.text().strip()
        if base and os.path.isdir(base):
            _open_path_cross_platform(base)


# =====================================
# Plugin main (toolbar = one button)
# =====================================

class AddressSearchToolbar:

    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.action_open = None
        self.dlg: Optional[AddressSearchDialog] = None

    def initGui(self):
        self.toolbar = self.iface.addToolBar("Address Search")
        self.toolbar.setObjectName("AddressSearchToolbarFieldSelect")

        self.action_open = QAction("検索", self.iface.mainWindow())
        self.action_open.setToolTip("属性検索ダイアログを開く")
        self.action_open.triggered.connect(self.open_dialog)
        self.toolbar.addAction(self.action_open)

    def unload(self):
        try:
            if self.toolbar:
                self.iface.mainWindow().removeToolBar(self.toolbar)
                self.toolbar = None
        except Exception:
            pass

    def open_dialog(self):
        if self.dlg is not None and self.dlg.isVisible():
            self.dlg.raise_()
            self.dlg.activateWindow()
            return

        self.dlg = AddressSearchDialog(self.iface, self.iface.mainWindow())
        self.dlg.show()
