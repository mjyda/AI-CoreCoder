#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本 - 你好世界
"""
import unittest

class TestHelloWorld(unittest.TestCase):
    """测试你好世界功能"""

    def test_hello_world(self):
        """测试输出'你好世界'"""
        message = "你好世界"
        self.assertEqual(message, "你好世界")

if __name__ == '__main__':
    unittest.main()
