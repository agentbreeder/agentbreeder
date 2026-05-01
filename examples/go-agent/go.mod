module github.com/agentbreeder/agentbreeder/examples/go-agent

go 1.22

require github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder v0.0.0

require github.com/go-chi/chi/v5 v5.2.2 // indirect

// Local replace so the example tracks the in-repo SDK without a published
// version. Drop this line and pin a tag once the SDK ships.
replace github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder => ../../sdk/go/agentbreeder
