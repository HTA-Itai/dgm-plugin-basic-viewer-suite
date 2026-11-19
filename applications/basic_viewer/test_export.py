import csv
import io
import unittest

from main import csv_text


class ExportTests(unittest.TestCase):
    def test_headers_and_values_are_neutralized(self):
        text = csv_text([{"=HYPERLINK(1)": "=1+1", "dx": -1.5, "note": "-x"},
                         {"@cmd": None, "dx": 2}])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["'=HYPERLINK(1)", "dx", "note", "'@cmd"])
        self.assertEqual(rows[1], ["'=1+1", "-1.5", "'-x", ""])
        self.assertEqual(rows[2], ["", "2", "", ""])

    def test_signed_numeric_text_stays_numeric(self):
        text = csv_text([{"DArea": "-45.190", "b": "+1e-3", "c": "-1+2", "d": "-.5", "e": "-1,000"}])
        self.assertEqual(list(csv.reader(io.StringIO(text)))[1], ["-45.190", "+1e-3", "'-1+2", "-.5", "'-1,000"])


if __name__ == "__main__":
    unittest.main()
