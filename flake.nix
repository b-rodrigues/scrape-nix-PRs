{
  description = "Python environment for scraping nixpkgs PRs";

  inputs = {
    nixpkgs.url = "github:rstats-on-nix/nixpkgs/2026-05-04";
  };

  outputs = { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (pythonPackages: with pythonPackages; [
              requests
            ]))
          ];
        };
      });
    };
}
