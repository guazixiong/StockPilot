"""新浪行业板块解析 + heat_color 动画色映射。"""
from stockpilot.core.providers.sina import SinaProvider


def _sample_hy_text():
    # 真实格式截取（GBK 解码后）
    return ('var S_Finance_bankuai_sinaindustry = {"new_blhy":"new_blhy,'
            '玻璃行业,19,16.944210526316,-0.18894736842105,-1.1028169446748,'
            '568270780,14190996315,sh600629,3.768,16.800,0.610,华建集团",'
            '"new_cbzz":"new_cbzz,船舶制造,8,13.877142857143,'
            '0.097142857142857,0.70495542193655,254031302,4787054873,'
            'sz300123,1.972,5.170,0.100,ST亚光"};')


def test_sina_board_parse(monkeypatch):
    def fake_get_text(self, url, **kw):
        return _sample_hy_text()
    monkeypatch.setattr("stockpilot.core.providers.base.HttpClient.get_text",
                        fake_get_text)
    from stockpilot.core.providers.base import HttpClient
    boards = SinaProvider(HttpClient()).get_industry_boards()
    assert len(boards) == 2
    b = boards[0]
    assert b.name == "玻璃行业"
    assert abs(b.change_pct - -1.1028) < 0.001
    assert abs(b.amount_yi - 141.90996315) < 0.01
    assert b.count == 19
    assert b.leader_code == "600629"
    assert b.leader_name == "华建集团"
    assert abs(b.leader_pct - 3.768) < 0.001
    assert boards[1].name == "船舶制造"


def test_heat_color_mapping():
    from stockpilot.ui.heat_widgets import heat_color

    def hex_r(c): return int(c[1:3], 16)
    def hex_g(c): return int(c[3:5], 16)

    red = heat_color(2.0)
    green = heat_color(-2.0)
    flat = heat_color(0.0)
    none_c = heat_color(None)
    assert red.startswith("#") and hex_r(red) > 200     # 亮红
    assert hex_g(green) > 180                            # 亮绿
    # 0% 为中性灰（R≈G，不偏红不偏绿）
    assert abs(hex_r(flat) - hex_g(flat)) <= 10
    assert flat == "#173351" or abs(hex_r(flat) - 58) < 30
    assert none_c == "#173351"
    # 涨>0 红、跌<0 绿 的方向性
    assert hex_r(heat_color(1)) > hex_r(heat_color(-1))
    assert hex_g(heat_color(-1)) > hex_g(heat_color(1))
