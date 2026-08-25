package htmltext

import "testing"

func TestPlainStripsMarkupAndDecodesEntities(t *testing.T) {
	cases := map[string]string{
		"<p>hello <b>world</b></p>":            "hello world",
		"a&amp;b &lt;tag&gt; &quot;q&quot;":    `a&b <tag> "q"`,
		"one<br>two":                           "one\ntwo",
		"<p>one</p><p>two</p>":                 "one\ntwo",
		"<div>  spaced   out  </div>":          "spaced out",
		"":                                     "",
		"<p>keep</p><script>evil()</script>":   "keep",
		"<p>keep</p><pre><code>x=1</code></pre>": "keep",
		"&nbsp;trim&nbsp;":                     "trim",
	}
	for in, want := range cases {
		if got := Plain(in); got != want {
			t.Errorf("Plain(%q)=%q want %q", in, got, want)
		}
	}
}

func TestPlainCollapsesBlankRuns(t *testing.T) {
	if got := Plain("<p>a</p><br><br><br><p>b</p>"); got != "a\n\nb" {
		t.Errorf("blank runs not collapsed: %q", got)
	}
}
