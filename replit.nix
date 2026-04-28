{ pkgs }: {
  # Reserved VM compatible: pin Python 3.11 + system libs needed by asyncpg, redis, openssl
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.python311Packages.setuptools
    pkgs.python311Packages.wheel
    pkgs.postgresql
    pkgs.redis
    pkgs.openssl
    pkgs.cacert
    pkgs.git
    pkgs.gcc
    pkgs.libffi
    pkgs.zlib
  ];

  env = {
    LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.openssl
      pkgs.libffi
      pkgs.zlib
    ];
    SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
  };
}
