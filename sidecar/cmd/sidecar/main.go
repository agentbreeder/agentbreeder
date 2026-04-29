// Command sidecar is the AgentBreeder cross-cutting-concerns proxy.
//
// One Go binary that fronts every deployed agent regardless of language. The
// binary is lazily entered: when AGENTBREEDER_SIDECAR=disabled the process
// exits successfully so docker-compose / k8s health probes don't churn.
package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/server"
)

// version is overridden at build time via -ldflags '-X main.version=...'
var version = "dev"

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	if config.IsDisabled() {
		logger.Info("AGENTBREEDER_SIDECAR=disabled — exiting cleanly", "version", version)
		os.Exit(0)
	}

	cfgPath := flag.String("config", config.DefaultConfigPath, "path to sidecar.yaml")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		logger.Error("config load failed", "err", err.Error())
		os.Exit(1)
	}

	srv, err := server.New(cfg, logger)
	if err != nil {
		logger.Error("server build failed", "err", err.Error())
		os.Exit(1)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	logger.Info("starting agentbreeder-sidecar",
		"version", version,
		"agent", cfg.AgentName,
		"inbound", cfg.InboundAddr,
		"local", cfg.A2AAddr,
	)

	if err := srv.Run(ctx); err != nil {
		logger.Error("server exited with error", "err", err.Error())
		os.Exit(1)
	}
	logger.Info("agentbreeder-sidecar shut down cleanly")
}
