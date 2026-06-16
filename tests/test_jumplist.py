"""
JumpList: push, back, forward, max_depth
"""

from peovim.core.jumplist import JumpList


class TestJumpListBasic:
    def test_empty_back(self):
        jl = JumpList()
        assert jl.back() is None

    def test_empty_forward(self):
        jl = JumpList()
        assert jl.forward() is None

    def test_push_single(self):
        jl = JumpList()
        jl.push(1, 0)
        assert jl.current() == ("", 1, 0, 0)

    def test_push_two_back(self):
        jl = JumpList()
        jl.push(0, 0)
        jl.push(5, 3)
        pos = jl.back()
        assert pos == ("", 0, 0, 0)

    def test_back_then_forward(self):
        jl = JumpList()
        jl.push(0, 0)
        jl.push(5, 3)
        jl.back()
        pos = jl.forward()
        assert pos == ("", 5, 3, 0)

    def test_back_at_start_returns_none(self):
        jl = JumpList()
        jl.push(1, 0)
        jl.back()
        assert jl.back() is None

    def test_forward_at_end_returns_none(self):
        jl = JumpList()
        jl.push(1, 0)
        jl.push(2, 0)
        assert jl.forward() is None

    def test_duplicate_not_pushed(self):
        jl = JumpList()
        jl.push(3, 5)
        jl.push(3, 5)  # duplicate
        assert len(jl) == 1


class TestJumpListNavigation:
    def test_multiple_back(self):
        jl = JumpList()
        for i in range(5):
            jl.push(i, 0)
        # At ("",4,0); back 3 times should land at ("",1,0)
        jl.back()  # ("",3,0)
        jl.back()  # ("",2,0)
        pos = jl.back()  # ("",1,0)
        assert pos == ("", 1, 0, 0)

    def test_push_truncates_forward(self):
        jl = JumpList()
        jl.push(0, 0)
        jl.push(1, 0)
        jl.push(2, 0)
        jl.back()  # at ("",1,0)
        jl.back()  # at ("",0,0)
        jl.push(9, 9)  # truncate forward, add new
        assert jl.forward() is None  # no forward from here
        assert jl.current() == ("", 9, 9, 0)

    def test_can_go_back_forward(self):
        jl = JumpList()
        jl.push(0, 0)
        jl.push(1, 0)
        assert jl.can_go_back()
        assert not jl.can_go_forward()
        jl.back()
        assert not jl.can_go_back()
        assert jl.can_go_forward()

    def test_len(self):
        jl = JumpList()
        assert len(jl) == 0
        jl.push(0, 0)
        jl.push(1, 0)
        assert len(jl) == 2


class TestJumpListMaxDepth:
    def test_max_depth_enforced(self):
        jl = JumpList(max_depth=5)
        for i in range(10):
            jl.push(i, 0)
        assert len(jl) == 5

    def test_oldest_dropped(self):
        jl = JumpList(max_depth=3)
        jl.push(0, 0)
        jl.push(1, 0)
        jl.push(2, 0)
        jl.push(3, 0)  # drops ("",0,0)
        # Go all the way back
        jl.back()  # ("",2,0)
        pos = jl.back()  # ("",1,0)
        assert pos == ("", 1, 0, 0)
        assert jl.back() is None  # nothing before ("",1,0)


class TestJumpListWithPath:
    def test_push_with_path(self):
        jl = JumpList()
        jl.push(0, 0, "/foo/bar.py")
        assert jl.current() == ("/foo/bar.py", 0, 0, 0)

    def test_dedup_uses_full_triple(self):
        jl = JumpList()
        jl.push(0, 0, "/a.py")
        jl.push(0, 0, "/b.py")  # same line/col, different path — not a duplicate
        assert len(jl) == 2

    def test_back_cross_file(self):
        jl = JumpList()
        jl.push(5, 3, "/a.py")
        jl.push(10, 0, "/b.py")
        pos = jl.back()
        assert pos == ("/a.py", 5, 3, 0)
