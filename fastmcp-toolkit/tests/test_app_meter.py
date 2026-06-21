"""ApplicationMeterHelper のテスト。"""

from unittest.mock import MagicMock, patch

from fastmcp_toolkit.metrics import ApplicationMeterHelper


class TestAppMeterCount:
    def test_creates_counter_on_first_call(self):
        """初回呼び出し時にカウンタが生成される。"""
        meter = ApplicationMeterHelper("test")
        mock_counter = MagicMock()
        with patch.object(meter._meter, "create_counter", return_value=mock_counter):
            meter.count("requests.total")

        mock_counter.add.assert_called_once_with(1, {})

    def test_reuses_counter_on_subsequent_calls(self):
        """2回目以降は同じカウンタインスタンスを再利用する。"""
        meter = ApplicationMeterHelper("test")
        mock_counter = MagicMock()
        meter._counters["requests.total"] = mock_counter

        meter.count("requests.total")
        meter.count("requests.total", value=5)

        assert mock_counter.add.call_count == 2
        assert mock_counter.add.call_args_list[0][0] == (1, {})
        assert mock_counter.add.call_args_list[1][0] == (5, {})

    def test_passes_attributes(self):
        """属性が正しく渡される。"""
        meter = ApplicationMeterHelper("test")
        mock_counter = MagicMock()
        meter._counters["errors"] = mock_counter

        meter.count("errors", attributes={"type": "timeout"})

        mock_counter.add.assert_called_once_with(1, {"type": "timeout"})

    def test_custom_value(self):
        """value引数でカウントを指定できる。"""
        meter = ApplicationMeterHelper("test")
        mock_counter = MagicMock()
        meter._counters["bytes.sent"] = mock_counter

        meter.count("bytes.sent", value=1024)

        mock_counter.add.assert_called_once_with(1024, {})


class TestAppMeterRecord:
    def test_creates_histogram_on_first_call(self):
        """初回呼び出し時にヒストグラムが生成される。"""
        meter = ApplicationMeterHelper("test")
        mock_histogram = MagicMock()
        with patch.object(
            meter._meter, "create_histogram", return_value=mock_histogram
        ):
            meter.record("db.row_count", 42)

        mock_histogram.record.assert_called_once_with(42, {})

    def test_reuses_histogram_on_subsequent_calls(self):
        """2回目以降は同じヒストグラムインスタンスを再利用する。"""
        meter = ApplicationMeterHelper("test")
        mock_histogram = MagicMock()
        meter._histograms["latency"] = mock_histogram

        meter.record("latency", 10.5)
        meter.record("latency", 20.3)

        assert mock_histogram.record.call_count == 2

    def test_passes_attributes(self):
        """属性が正しく渡される。"""
        meter = ApplicationMeterHelper("test")
        mock_histogram = MagicMock()
        meter._histograms["query.rows"] = mock_histogram

        meter.record("query.rows", 100, {"table": "users"})

        mock_histogram.record.assert_called_once_with(100, {"table": "users"})


class TestAppMeterGauge:
    def test_creates_up_down_counter_on_first_call(self):
        """初回呼び出し時にUpDownCounterが生成される。"""
        meter = ApplicationMeterHelper("test")
        mock_udc = MagicMock()
        with patch.object(
            meter._meter, "create_up_down_counter", return_value=mock_udc
        ):
            meter.gauge("connections.active", 3)

        mock_udc.add.assert_called_once_with(3, {})

    def test_reuses_up_down_counter_on_subsequent_calls(self):
        """2回目以降は同じインスタンスを再利用する。"""
        meter = ApplicationMeterHelper("test")
        mock_udc = MagicMock()
        meter._up_down_counters["pool.size"] = mock_udc

        meter.gauge("pool.size", 5)
        meter.gauge("pool.size", -2)

        assert mock_udc.add.call_count == 2
        assert mock_udc.add.call_args_list[0][0] == (5, {})
        assert mock_udc.add.call_args_list[1][0] == (-2, {})

    def test_passes_attributes(self):
        """属性が正しく渡される。"""
        meter = ApplicationMeterHelper("test")
        mock_udc = MagicMock()
        meter._up_down_counters["queue.depth"] = mock_udc

        meter.gauge("queue.depth", 10, {"queue": "high-priority"})

        mock_udc.add.assert_called_once_with(10, {"queue": "high-priority"})


class TestAppMeterIsolation:
    def test_different_names_create_separate_instruments(self):
        """異なるメトリクス名は別々のインスタンスを持つ。"""
        meter = ApplicationMeterHelper("test")
        mock_counter_a = MagicMock()
        mock_counter_b = MagicMock()
        meter._counters["a"] = mock_counter_a
        meter._counters["b"] = mock_counter_b

        meter.count("a")
        meter.count("b", value=2)

        mock_counter_a.add.assert_called_once_with(1, {})
        mock_counter_b.add.assert_called_once_with(2, {})
