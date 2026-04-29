// Package main is the AgentBreeder thin wrapper for Go custom-framework
// agents. The runtime builder injects this file into the build context only
// when the user has not provided their own main.go.
//
// Behavior: bind on $PORT (or :8080), echo input, return a string response.
// The intent is "minimum lovable agent" — replace this file with your own
// once you've validated the deploy pipeline end-to-end.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	addr := ":" + envOr("PORT", "8080")

	srv := agentbreeder.NewServer(
		invoke,
		agentbreeder.WithFramework("custom"),
		agentbreeder.WithLogger(logger),
	)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if err := srv.ListenAndServe(ctx, addr); err != nil {
		logger.Error("server failed", "err", err.Error())
		os.Exit(1)
	}
}

func invoke(_ context.Context, req agentbreeder.InvokeRequest, resp *agentbreeder.InvokeResponse) error {
	if s, ok := req.Input.AsString(); ok {
		return resp.SetOutput(fmt.Sprintf("Echo: %s", s))
	}
	var obj map[string]any
	if err := req.Input.AsObject(&obj); err == nil {
		return resp.SetOutput(map[string]any{"echo": obj})
	}
	return resp.SetOutput("ok")
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
