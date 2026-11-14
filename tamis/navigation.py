"""Moving between photos in the Cull view, "Keeps only" and Compare.

`NavigationMixin` is part of `TamisApp`.
"""

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QAbstractItemView

from .constants import CULL_TAB, GALLERY_TAB, KEEP, PREFETCH_AHEAD, PREFETCH_BEHIND, is_raw
from .models import StatusFilterProxy
from .plugins import PhotoInfo

COMPARE_MAX = 4


class NavigationMixin:
    # -- navigation --------------------------------------------------------

    def go_to(self, row):
        if not 0 <= row < self.model.rowCount():
            return
        moved_on = row != self.current_row
        if moved_on:
            self._finish_crop(apply=True)  # moving on keeps the frame, like any other edit
        if self.compare_rows and row not in self.compare_rows:
            self._end_compare(show=False)
        self.current_row = row
        photo = self.model.photo(row)
        self._update_counter()
        self.header.set_folder(self.folder, photo.display_name)

        # Invalid when the filmstrip's filter hides this photo (opened from the gallery).
        index = self.strip_proxy.mapFromSource(self.model.index(row))
        self.filmstrip.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        if index.isValid():
            self.filmstrip.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

        # Request first: a photo that failed before is retried, and shows "Loading…".
        nearby = [*self._walk(row, 1, PREFETCH_AHEAD), *self._walk(row, -1, PREFETCH_BEHIND)]
        others = [r for r in self._shown_rows() if r != row] + nearby
        self.loader.ensure_previews(photo.path, [self.model.photo(r).path for r in dict.fromkeys(others)])
        self.live_edit = None
        self.edit_panel.set_edit(photo.edit, is_raw(photo.path))
        self._refresh_view()
        if moved_on:
            self.plugins.emit("photo_changed", PhotoInfo.of(photo))

    def _in_cull(self, row):
        """Whether the Cull view's filter lets this photo through."""
        return not self.keeps_only or self.model.photo(row).status == KEEP

    def _cull_neighbor(self, row, delta):
        """The closest photo the Cull view shows past `row`, going in the direction of `delta`; None if none."""
        row += delta
        while 0 <= row < self.model.rowCount():
            if self._in_cull(row):
                return row
            row += delta
        return None

    def _walk(self, row, delta, count):
        """Up to `count` photos the Cull view shows past `row`."""
        rows = []
        while len(rows) < count and (row := self._cull_neighbor(row, delta)) is not None:
            rows.append(row)
        return rows

    def _update_counter(self):
        if self.current_row < 0:
            return
        position = self.strip_proxy.mapFromSource(self.model.index(self.current_row))
        if self.keeps_only and position.isValid():
            self.header.set_counter(f"{position.row() + 1} / {self.strip_proxy.rowCount()} keeps")
        else:
            self.header.set_counter(f"{self.current_row + 1} / {self.model.rowCount()}")

    def step(self, delta):
        """Next / previous photo, or the next / previous one being compared."""
        if self.compare_rows:
            i = self.compare_rows.index(self.current_row) + delta
            if 0 <= i < len(self.compare_rows):
                self.go_to(self.compare_rows[i])
            return
        row = self._cull_neighbor(self.current_row, delta)
        if row is not None:
            self.go_to(row)

    def go_to_end(self, first):
        """The first or last photo the Cull view shows."""
        row = self._cull_neighbor(-1, 1) if first else self._cull_neighbor(self.model.rowCount(), -1)
        if row is not None:
            self.go_to(row)

    def toggle_keeps_only(self):
        on = not self.keeps_only
        if on and not self.model.counts()[0]:
            self.keeps_button.setChecked(False)
            self.toast.show_message("No keeps yet: mark photos with P")
            return
        self._set_keeps_only(on)
        self.toast.show_message("Showing keeps only" if on else "Showing all photos")

    def _set_keeps_only(self, on):
        if on == self.keeps_only:
            return
        self._finish_crop(apply=True)
        self._end_compare(show=False)
        self.keeps_only = on
        self.keeps_button.setChecked(on)
        self.strip_proxy.set_filter(KEEP if on else StatusFilterProxy.ALL)
        self._keep_current_in_filter()
        if self.current_row >= 0 and self._in_cull(self.current_row):
            self.go_to(self.current_row)  # the filmstrip was just rebuilt

    def _keep_current_in_filter(self):
        """With "Keeps only", move on when the current photo stops being a keep; stop filtering when none is left."""
        if (
            not self.keeps_only or self.current_row < 0 or self.compare_rows
            or self.tabs.currentIndex() != CULL_TAB or self._in_cull(self.current_row)
        ):
            return
        row = self._cull_neighbor(self.current_row, 1)
        if row is None:
            row = self._cull_neighbor(self.current_row, -1)
        if row is not None:
            self.go_to(row)
        else:
            self._set_keeps_only(False)
            self.toast.show_message("No keeps left · showing all photos")

    def next_unmarked(self):
        if self.keeps_only:
            self._set_keeps_only(False)  # an unmarked photo is not a keep: asking for one ends the filter
        count = self.model.rowCount()
        for offset in range(1, count + 1):
            row = (self.current_row + offset) % count
            if self.model.photo(row).status is None:
                self.go_to(row)
                return
        self.toast.show_message("Every photo is marked")

    def toggle_tab(self):
        self.tabs.setCurrentIndex(GALLERY_TAB if self.tabs.currentIndex() == CULL_TAB else CULL_TAB)

    # -- compare -----------------------------------------------------------

    def toggle_compare(self):
        """Gallery: compare the selected photos. Cull: the current photo and the next one."""
        if self.compare_rows:
            self._end_compare()
            return
        self._finish_crop(apply=True)
        if self.tabs.currentIndex() == GALLERY_TAB:
            rows = sorted(self.proxy.mapToSource(i).row() for i in self.grid.selectionModel().selectedIndexes())
            if not 2 <= len(rows) <= COMPARE_MAX:
                self.toast.show_message(f"Select 2 to {COMPARE_MAX} photos to compare")
                return
            if not all(self._in_cull(row) for row in rows):
                self._set_keeps_only(False)  # asked for by name, whatever the Cull view's filter
        elif self.current_row >= 0 and (following := self._cull_neighbor(self.current_row, 1)) is not None:
            rows = [self.current_row, following]
        else:
            self.toast.show_message("No next photo to compare with")
            return

        self.compare_rows = rows
        self.compare_view.set_count(len(rows))
        self.cull_stack.setCurrentWidget(self.compare_view)
        self.tabs.setCurrentIndex(CULL_TAB)
        self.go_to(self.current_row if self.current_row in rows else rows[0])

    def _end_compare(self, show=True):
        if not self.compare_rows:
            return
        self.compare_rows = []
        self.compare_view.reset_zoom()
        self.cull_stack.setCurrentWidget(self.image_view)
        if show:
            self.go_to(self.current_row)
            self._keep_current_in_filter()  # it may have been rejected while being compared
