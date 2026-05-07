{
  description = "Python environment for scraping nixpkgs PRs";

  inputs = {
    nixpkgs.url = "github:rstats-on-nix/nixpkgs/2026-05-04";
  };

  outputs = { self, nixpkgs, ... }:
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
      packages = forAllSystems (pkgs: {
        default = pkgs.python3.withPackages (pythonPackages: with pythonPackages; [
          requests
        ]);
      });

      apps = forAllSystems (pkgs: {
        find-prs = {
          type = "app";
          program = "${pkgs.writeShellScriptBin "find-prs" ''
            ${pkgs.python3.withPackages (ps: [ ps.requests ])}/bin/python3 ${./find_reviewed_prs.py} "$@"
          ''}/bin/find-prs";
        };
        scrape-prs = {
          type = "app";
          program = "${pkgs.writeShellScriptBin "scrape-prs" ''
            ${pkgs.python3.withPackages (ps: [ ps.requests ])}/bin/python3 ${./scrape_nixpkgs_prs.py} "$@"
          ''}/bin/scrape-prs";
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          name = "scrape-nix-PRs-shell";
          packages = [
            (pkgs.python3.withPackages (pythonPackages: with pythonPackages; [
              requests
            ]))
          ];
          shellHook = ''
            echo "Scrape Nix PRs development environment"
            echo "Available scripts: find_reviewed_prs.py, scrape_nixpkgs_prs.py"
          '';
        };
      });
    };
}
