class GhosttyToggle < Formula
  include Language::Python::Virtualenv

  desc "CLI for discovering Ghostty options and toggling config values"
  homepage "https://github.com/your-org/ghostty-toggle"
  url "https://github.com/your-org/ghostty-toggle/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_SHA256"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "ghostty-toggle", shell_output("#{bin}/ghostty-toggle --help")
  end
end
