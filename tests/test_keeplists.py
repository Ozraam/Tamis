import csv
import io
import os

from tamis import keeplists
from tamis.constants import KEEP, REJECT
from tamis.models import Photo


def photos(folder):
    return [
        Photo(os.path.join(folder, "_KEEPS", "IMG_1.CR2"), KEEP, KEEP, rating=4, label="Green",
              companions=[os.path.join(folder, "_KEEPS", "IMG_1.JPG")]),
        Photo(os.path.join(folder, "IMG 2.jpg"), REJECT),
        Photo(os.path.join(folder, "IMG_3.jpg")),
    ]


def test_lightroom_gets_stems_once_each_quoted_when_they_have_a_space(tmp_path):
    assert keeplists.text(photos(str(tmp_path)), "lightroom") == 'IMG_1 "IMG 2" IMG_3'


def test_one_name_per_line_lists_the_companions_too(tmp_path):
    assert keeplists.text(photos(str(tmp_path)), "lines") == "IMG_1.CR2\nIMG_1.JPG\nIMG 2.jpg\nIMG_3.jpg\n"


def test_csv_has_a_row_per_file_with_the_marks(tmp_path):
    rows = list(csv.reader(io.StringIO(keeplists.text(photos(str(tmp_path)), "csv", str(tmp_path)))))
    assert rows[0] == ["name", "mark", "stars", "label", "folder"]
    assert rows[1:] == [
        ["IMG_1.CR2", "keep", "4", "Green", "_KEEPS"],
        ["IMG_1.JPG", "keep", "4", "Green", "_KEEPS"],
        ["IMG 2.jpg", "reject", "0", "", ""],
        ["IMG_3.jpg", "", "0", "", ""],
    ]


def test_an_empty_list_is_empty():
    assert keeplists.text([], "lightroom") == ""
    assert keeplists.text([], "lines") == ""


def test_default_name_is_the_shoot_s(tmp_path):
    folder = str(tmp_path / "Wedding")
    assert keeplists.default_name(folder + os.sep, "csv") == "Wedding keeps.csv"
    assert keeplists.default_name(folder, "lines", "shown") == "Wedding shown.txt"
