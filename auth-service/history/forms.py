from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model


class HistoryImportForm(forms.Form):
    history_file = forms.FileField(label="JSONL 文件")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.is_superuser:
            self.fields["owner_id"] = forms.ModelChoiceField(
                label="归属账号",
                queryset=get_user_model().objects.filter(is_active=True).order_by("username"),
                required=False,
                empty_label="当前管理员账号",
            )

    def clean_history_file(self):
        uploaded = self.cleaned_data["history_file"]
        suffix = Path(uploaded.name).suffix.lower()
        if suffix not in {".jsonl", ".json"}:
            raise forms.ValidationError("只接受 .jsonl 或 .json 文件。")
        max_bytes = int(getattr(settings, "HISTORY_IMPORT_MAX_BYTES", 25 * 1024 * 1024))
        if uploaded.size > max_bytes:
            raise forms.ValidationError(f"文件不能超过 {max_bytes} 字节。")
        return uploaded


class MemoryPoolForm(forms.Form):
    memory_file = forms.FileField(label="本地 MEMORY.md", required=False)
    user_file = forms.FileField(label="本地 USER.md", required=False)
    memory_markdown = forms.CharField(
        label="MEMORY.md 内容",
        required=False,
        widget=forms.Textarea(attrs={"rows": 16}),
    )
    user_markdown = forms.CharField(
        label="USER.md 内容",
        required=False,
        widget=forms.Textarea(attrs={"rows": 16}),
    )

    _MAX_CHARS = 200_000

    def _clean_markdown(self, field_name, file_field_name):
        uploaded = self.files.get(file_field_name)
        if uploaded is not None:
            suffix = Path(uploaded.name).suffix.lower()
            if suffix not in {".md", ".markdown"}:
                raise forms.ValidationError("只接受 .md 或 .markdown 文件。")
            if uploaded.size > self._MAX_CHARS * 4:
                raise forms.ValidationError("Memory 文件过大。")
            try:
                value = uploaded.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError("Memory 文件必须使用 UTF-8 编码。") from exc
        else:
            if field_name not in self.data:
                return None
            value = self.cleaned_data.get(field_name, "")
        if len(value) > self._MAX_CHARS:
            raise forms.ValidationError("Memory 内容不能超过 200000 个字符。")
        return value

    def clean_memory_markdown(self):
        return self._clean_markdown("memory_markdown", "memory_file")

    def clean_user_markdown(self):
        return self._clean_markdown("user_markdown", "user_file")
