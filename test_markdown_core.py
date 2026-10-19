from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from markdown_core import (DEFAULT_MARKDOWN, MAX_MARKDOWN_CHARS, read_markdown_file,
                           render_markdown, safe_link, write_markdown_file)
from palette_core import CATEGORIES, Settings, SettingsStore


def plain(source):
    return "".join(run.text for run in render_markdown(source))


class MarkdownRenderingTests(unittest.TestCase):
    def test_heading_and_nested_inline_styles(self):
        runs = render_markdown("# **标题**\n\n正文 *斜体* ~~删除~~ `code`")
        self.assertTrue(any(run.text == "标题" and {"h1", "bold"} <= set(run.styles) for run in runs))
        for word, style in (("斜体", "italic"), ("删除", "strike"), ("code", "code")):
            self.assertTrue(any(run.text == word and style in run.styles for run in runs))

    def test_tasks_nested_lists_and_ordered_start(self):
        text = plain("- [ ] 未完成\n  - 子项\n- [x] 已完成\n\n3. 第三项\n4. 第四项\n")
        for fragment in ("☐ 未完成", "  • 子项", "☑ 已完成", "3. 第三项", "4. 第四项"):
            self.assertIn(fragment, text)
        self.assertNotIn("[ ]", text)

    def test_quotes_and_fenced_code_preserve_code(self):
        code = "if x < 2:\n    print('**literal**')\n"
        runs = render_markdown("> 引用内容\n\n```python\n" + code + "```\n\n---\n")
        self.assertTrue(any("引用内容" in run.text and "quote" in run.styles for run in runs))
        self.assertTrue(any(run.text == code and "code_block" in run.styles for run in runs))
        self.assertIn("────", "".join(run.text for run in runs))

    def test_tables_retain_chinese_and_inline_text(self):
        text = plain("| 名称 | 数量 |\n| --- | --- |\n| **笔记** | `2` |\n")
        for fragment in ("名称", "数量", "笔记", "2", "│"):
            self.assertIn(fragment, text)
        self.assertNotIn("**", text)

    def test_links_are_explicit_and_images_remain_offline_text(self):
        runs = render_markdown("[官网](https://example.com) ![示意](https://example.com/image.png)")
        self.assertTrue(any(run.text == "官网" and run.href == "https://example.com" for run in runs))
        self.assertIn("[图片：示意]", "".join(run.text for run in runs))
        self.assertFalse(any(run.href and run.href.endswith('.png') for run in runs))

    def test_raw_html_and_script_stay_plain_text(self):
        text = plain('<script>alert(1)</script>\n\n<b>原样显示</b>')
        self.assertIn('<script>alert(1)</script>', text)
        self.assertIn('<b>原样显示</b>', text)

    def test_escaped_markdown_is_not_formatted(self):
        runs = render_markdown(r'\*普通星号\* and \`code\`')
        self.assertIn('*普通星号* and `code`', ''.join(run.text for run in runs))
        self.assertFalse(any('italic' in run.styles or 'code' in run.styles for run in runs))

    def test_blank_and_oversized_documents(self):
        self.assertEqual(render_markdown(""), ())
        with self.assertRaises(ValueError):
            render_markdown("字" * (MAX_MARKDOWN_CHARS + 1))

    def test_only_explicit_web_or_mail_links_can_open(self):
        for value in ('https://example.com/a', 'http://example.com/', 'mailto:user@example.com'):
            self.assertEqual(safe_link(value), value)
        for value in ('javascript:alert(1)', 'file:///etc/passwd', '/tmp/local.md', 'https://', 'https://a\nb'):
            self.assertIsNone(safe_link(value))


class MarkdownStorageTests(unittest.TestCase):
    def test_utf8_import_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'我的笔记.md'
            path.write_bytes(b'\xef\xbb\xbf' + '# 标题\r\n\r\n- [ ] 待办'.encode('utf-8'))
            text = read_markdown_file(path)
            self.assertEqual(text, '# 标题\n\n- [ ] 待办')
            write_markdown_file(path, text + '\n')
            self.assertEqual(path.read_bytes(), (text + '\n').encode('utf-8'))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_export_keeps_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'note.md'
            path.write_text('original')
            with patch('markdown_core.os.replace', side_effect=OSError('not writable')):
                with self.assertRaises(OSError):
                    write_markdown_file(path, 'draft')
            self.assertEqual(path.read_text(), 'original')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_or_oversized_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'note.md'
            for data, error in ((b'\xff', UnicodeDecodeError), (b'a\0b', ValueError),
                                (b'a' * (MAX_MARKDOWN_CHARS + 1), ValueError)):
                path.write_bytes(data)
                with self.assertRaises(error):
                    read_markdown_file(path)

    def test_existing_custom_cards_migrate_without_losing_originals(self):
        raw = {'side': 'left', 'collapsed': True, 'notes': [
            {'color': '#abcdef', 'color_name': '待办', 'title': '我的提示', 'description': '第一行\n第二行'}],
            'guidance': '下次继续'}
        settings = Settings.from_dict(raw)
        self.assertEqual(settings.content_mode, 'markdown')
        self.assertIn('我的提示', settings.markdown)
        self.assertIn('第一行\n第二行', settings.markdown)
        self.assertEqual(settings.notes[0].description, raw['notes'][0]['description'])
        self.assertEqual(settings.side, 'left')
        self.assertTrue(settings.collapsed)

    def test_markdown_empty_document_and_mode_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory))
            settings = Settings(content_mode='markdown', markdown='')
            store.save(settings)
            self.assertEqual(store.load(), settings)
            settings.markdown = '# 自己的文档\n\n' + '正文\n' * 2000
            settings.content_mode = 'palette'
            store.save(settings)
            self.assertEqual(store.load(), settings)

    def test_default_starter_and_old_color_template_are_both_available(self):
        self.assertEqual(Settings.from_dict({}).markdown, DEFAULT_MARKDOWN)
        self.assertEqual(Settings.from_dict({}).notes, CATEGORIES)
        self.assertEqual(Settings.from_dict({'notes': [], 'guidance': ''}).markdown, '')

    def test_loading_never_silently_truncates_a_saved_document(self):
        text = '字' * (MAX_MARKDOWN_CHARS + 100)
        self.assertEqual(Settings.from_dict({'markdown': text}).markdown, text)

    def test_upgrade_renames_only_the_stock_title(self):
        stock = {'title': '论文阅读色卡', 'subtitle': '八种颜色，一套阅读语言'}
        self.assertEqual(Settings.from_dict(stock).title, '悬浮便签')
        custom = {**stock, 'title': '自己的阅读清单'}
        self.assertEqual(Settings.from_dict(custom).title, custom['title'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
