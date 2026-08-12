"""Tests for the email and spreadsheet parsers.

Several cases here are marked as regressions. They came from running real input
through the stack during Phase 3/4, when parsing lived in the Node tier, and
every one of them failed silently -- a corrupted part number and a sentence in
the results table both look like data until someone reads them. They are ported
here because that is where parsing lives now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parsing import parse_spreadsheet, parse_text
from app.parsing.tokens import best_candidate, looks_like_part_number, score_token

SAMPLES = Path(__file__).resolve().parents[2] / "sample-rfqs"


def inputs(result) -> list[str]:
    return [item.input_string for item in result.items]


def quantities(result) -> list[int | None]:
    return [item.quantity for item in result.items]


# ---------------------------------------------------------------------------
# Token scoring
# ---------------------------------------------------------------------------

class TestTokenScoring:
    @pytest.mark.parametrize("token", [
        "STM32F103C8T6", "CRCW060310K0FKEA", "SN74LVC1G08DBVR", "PIC16F877A",
        "JANTX2N2222A", "D38999/24JA103SN", "BZX84-C10,215", "MCP645-I/SN",
        "1N4148", "LD1117V18", "296-STM32F130C3Y6-ND", "adp6208armz-2.5",
    ])
    def test_real_part_shapes_score_high(self, token):
        assert looks_like_part_number(token), f"{token} scored {score_token(token):.2f}"

    @pytest.mark.parametrize("token", [
        "the", "following", "urgent", "Thanks", "Regards",
        "dana@acme.example", "https://example.com", "$2.50", "2026-08-11",
        "500", "1,000", "10:30", "+442079460958", "ASAP", "RFQ",
    ])
    def test_non_parts_score_low(self, token):
        assert not looks_like_part_number(token), f"{token} scored {score_token(token):.2f}"

    def test_letters_and_digits_are_required(self):
        assert score_token("WAREHOUSE") == 0.0
        assert score_token("12345678") == 0.0

    def test_best_candidate_picks_the_part_out_of_prose(self):
        token, score = best_candidate("please quote STM32F103C8T6 for us")
        assert token == "STM32F103C8T6"
        assert score > 0.8

    def test_best_candidate_returns_nothing_for_pure_prose(self):
        token, _ = best_candidate("please let me know on lead times")
        assert token == ""


# ---------------------------------------------------------------------------
# Email / free text
# ---------------------------------------------------------------------------

class TestEmailParser:
    def test_one_part_per_line(self):
        result = parse_text("STM32F130ZBT6\nMMSZ4744AT1G")
        assert inputs(result) == ["STM32F130ZBT6", "MMSZ4744AT1G"]

    @pytest.mark.parametrize("line,mpn,qty", [
        ("STM32F130ZBT6 x 500", "STM32F130ZBT6", 500),
        ("STM32F130ZBT6 QTY: 500", "STM32F130ZBT6", 500),
        ("STM32F130ZBT6 qty=500", "STM32F130ZBT6", 500),
        ("500 pcs STM32F130ZBT6", "STM32F130ZBT6", 500),
        ("STM32F130ZBT6, 500, target $2.50", "STM32F130ZBT6", 500),
        ("STM32F130ZBT6\t500", "STM32F130ZBT6", 500),
        ("need 500 of STM32F130ZBT6", "STM32F130ZBT6", 500),
    ])
    def test_quantity_phrasings(self, line, mpn, qty):
        result = parse_text(line)
        assert inputs(result) == [mpn]
        assert quantities(result) == [qty]

    def test_bulleted_and_numbered_lists(self):
        result = parse_text(
            "1. STM32F130ZBT6 x 10\n"
            "2) MMSZ4744AT1G x 20\n"
            "- AD2736BRZ x 30\n"
            "* LD1117V18 x 40\n"
            "• PIC16F877A x 50"
        )
        assert quantities(result) == [10, 20, 30, 40, 50]
        assert len(result.items) == 5

    def test_thousands_separator_is_one_number(self):
        """Regression: a bare-comma split turned '1,000 pcs' into a second
        column and left the part number as 'SST25VF032B-75I  1'."""
        result = parse_text("- SST25VF032B-75I  1,000 pcs")
        assert inputs(result) == ["SST25VF032B-75I"]
        assert quantities(result) == [1000]

    def test_comma_inside_a_part_number_survives(self):
        """Regression: Nexperia really ships BZX84-C10,215. A bare-comma split
        silently truncated it to BZX84-C10."""
        result = parse_text("BZX84-C10,215 x 5000")
        assert inputs(result) == ["BZX84-C10,215"]
        assert quantities(result) == [5000]

    def test_prose_never_becomes_a_line_item(self):
        """Regression: these became rows in the results table."""
        result = parse_text(
            "Hi Jane,\n"
            "Please quote the following, needed ASAP:\n"
            "STM32F130ZBT6 x 5\n"
            "Let me know on lead times.\n"
            "Thanks,\n"
            "Bob"
        )
        assert inputs(result) == ["STM32F130ZBT6"]
        assert "Bob" in result.skipped
        assert "Please quote the following, needed ASAP:" in result.skipped

    def test_target_price_is_not_read_as_a_quantity(self):
        result = parse_text("STM32F130ZBT6 target $2.50 need 40")
        assert quantities(result) == [40]

    def test_quoted_reply_chain_is_dropped(self):
        result = parse_text(
            "STM32F130ZBT6 x 10\n"
            "> On Tue, someone wrote:\n"
            "> LM317T x 9999\n"
            "> please check these"
        )
        assert inputs(result) == ["STM32F130ZBT6"]
        assert result.diagnostics["quoted_lines_skipped"] == 3

    def test_signature_delimiter_ends_the_document(self):
        result = parse_text("STM32F130ZBT6 x 10\n--\nDana Whitfield\nMMSZ4744AT1G x 99")
        assert inputs(result) == ["STM32F130ZBT6"]
        assert result.diagnostics["signature_stripped"] is True

    def test_mail_headers_are_dropped(self):
        result = parse_text(
            "From: a@b.example\nSubject: RFQ 123\nDate: Tue\n\nSTM32F130ZBT6 x 1"
        )
        assert inputs(result) == ["STM32F130ZBT6"]

    def test_pasted_table_header_is_dropped(self):
        result = parse_text("Part Number\tQty\nSTM32F130ZBT6\t250")
        assert inputs(result) == ["STM32F130ZBT6"]
        assert quantities(result) == [250]

    def test_messy_input_is_passed_through_for_the_matcher(self):
        # Normalisation belongs to the matcher; the parser must not pre-chew it.
        result = parse_text("296-STM32F130C3Y6-ND x 500")
        assert inputs(result) == ["296-STM32F130C3Y6-ND"]

    def test_source_lines_are_recorded(self):
        result = parse_text("junk prose here\nSTM32F130ZBT6 x 1")
        assert result.items[0].source_line == 2

    def test_max_items_is_respected(self):
        text = "\n".join(f"STM32F130ZB{i}6 x 1" for i in range(50))
        assert len(parse_text(text, max_items=10).items) == 10

    def test_empty_input(self):
        assert parse_text("").items == []
        assert parse_text("\n\n   \n").items == []

    def test_the_sample_email_parses_end_to_end(self):
        result = parse_text((SAMPLES / "rfq-email.txt").read_text(encoding="utf-8"))
        found = inputs(result)

        assert len(found) == 12, f"expected 12 line items, got {found}"
        for expected in ["296-STM32F130C3Y6-ND", "stm32f105kct7", "MMSZ4744ATIG",
                         "D38999/24JA103SN", "SST25VF032B-75I", "BZX84-C10,215",
                         "PSMN10V4-60YL-TR", "adp6208armz-2.5", "MCP6001-E/SN"]:
            assert expected in found, f"{expected} missing from {found}"

        # The signature block, the phone number and the reply chain must not
        # produce line items -- LM317T in particular was someone else's ask.
        assert not any("LM317T" in item for item in found)
        assert not any("@" in item for item in found)
        assert quantities(result)[:4] == [500, 250, 2000, 12]


# ---------------------------------------------------------------------------
# Spreadsheets
# ---------------------------------------------------------------------------

class TestCleanBom:
    @pytest.fixture(scope="class")
    def result(self):
        return parse_spreadsheet((SAMPLES / "clean-bom.xlsx").read_bytes(), "clean-bom.xlsx")

    def test_reads_every_row(self, result):
        assert len(result.items) == 6

    def test_finds_the_columns(self, result):
        assert result.diagnostics["part_column"] == "A"
        assert result.diagnostics["quantity_column"] == "C"

    def test_quantities_are_integers_not_floats(self, result):
        # openpyxl returns 500.0 for a cell typed as 500; "500.0" would neither
        # look like a quantity nor parse as one.
        assert quantities(result) == [500, 2500, 10000, 2000, 750, 250]

    def test_skips_the_header_row(self, result):
        assert "Part Number" not in inputs(result)


class TestMessyBom:
    """Header four rows down, quantity to the LEFT, merged cells, decoy sheet."""

    @pytest.fixture(scope="class")
    def result(self):
        return parse_spreadsheet((SAMPLES / "messy-bom.xlsx").read_bytes(), "messy-bom.xlsx")

    def test_picks_the_bom_sheet_over_the_bigger_decoy(self, result):
        # The Instructions sheet has more rows. Choosing by size would be wrong.
        assert result.diagnostics["sheet"] == "Rev C"

    def test_finds_a_header_that_is_not_in_row_one(self, result):
        assert result.diagnostics["header_row"] == 5  # 0-indexed: data starts row 6

    def test_identifies_the_part_column_by_content_not_header_text(self, result):
        # The header is "Component", which no header-name list would rank above
        # "Description" -- content is what settles it.
        assert result.diagnostics["part_column"] == "B"
        assert result.diagnostics["part_column_header"] == "Component"

    def test_finds_a_quantity_column_to_the_left_of_the_parts(self, result):
        assert result.diagnostics["quantity_column"] == "A"
        assert quantities(result) == [500, 250, 1000, 75, 5000, 100, 12]

    def test_reads_every_part(self, result):
        assert len(result.items) == 7
        assert "BZX84-C10,215" in inputs(result)
        assert "D38999/24JA103SN" in inputs(result)

    def test_ignores_the_title_block(self, result):
        assert not any("ACME" in item.upper() for item in inputs(result))

    def test_merged_note_row_does_not_become_a_part(self, result):
        assert not any("NCNR" in item for item in inputs(result))

    def test_price_column_is_not_mistaken_for_quantity(self, result):
        assert result.diagnostics["quantity_column"] != "D"


class TestSpreadsheetEdgeCases:
    def test_a_file_with_no_parts_reports_why(self):
        from openpyxl import Workbook
        import io

        workbook = Workbook()
        sheet = workbook.active
        for row in [["Notes"], ["Please call to discuss"], ["Terms Net 45"]]:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = parse_spreadsheet(buffer.getvalue(), "notes.xlsx")
        assert result.items == []
        assert "reason" in result.diagnostics

    def test_a_bom_with_no_quantity_column_still_reads_parts(self):
        from openpyxl import Workbook
        import io

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MPN", "Description"])
        sheet.append(["STM32F130C3Y6", "MCU"])
        sheet.append(["MMSZ4744AT1G", "Zener"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = parse_spreadsheet(buffer.getvalue(), "no-qty.xlsx")
        assert len(result.items) == 2
        assert quantities(result) == [None, None]
