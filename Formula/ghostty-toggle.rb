class GhosttyToggle < Formula
  include Language::Python::Virtualenv

  desc "Terminal UI for exploring and editing Ghostty config"
  homepage "https://github.com/Xanaxxxxxx/ghostty-toggle"
  url "https://github.com/Xanaxxxxxx/ghostty-toggle/archive/refs/tags/v0.1.3.tar.gz"
  sha256 "7cf203d74e817e766ad6633ee8fdcbe8162fb3d9db62a029883fb875943bd3a3"
  license "MIT"

  depends_on "python@3.13"

  resource "prompt_toolkit" do
    url "https://files.pythonhosted.org/packages/source/p/prompt_toolkit/prompt_toolkit-3.0.52.tar.gz"
    sha256 "28cde192929c8e7321de85de1ddbe736f1375148b02f2e17edd840042b1be855"
  end

  resource "wcwidth" do
    url "https://files.pythonhosted.org/packages/source/w/wcwidth/wcwidth-0.2.13.tar.gz"
    sha256 "72ea0c06399eb286d978fdedb6923a9eb47e1c486ce63e9b4e64fc18303972b5"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "ghostty-toggle", shell_output("#{bin}/ghostty-toggle --help")
  end
end
