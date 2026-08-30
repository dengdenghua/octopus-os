import { bench, describe } from "vitest";

// Benchmark data structures
describe("Data Structure Operations", () => {
  const largeArray = Array.from({ length: 10000 }, (_, i) => ({
    id: i,
    data: `item-${i}`,
    nested: { value: i * 2 },
  }));

  bench("Array find", () => {
    largeArray.find((item) => item.id === 5000);
  });

  bench("Array filter", () => {
    largeArray.filter((item) => item.id % 2 === 0);
  });

  bench("Array map", () => {
    largeArray.map((item) => ({ ...item, computed: item.id * 3 }));
  });

  bench("Array reduce", () => {
    largeArray.reduce((sum, item) => sum + item.id, 0);
  });

  const largeMap = new Map(largeArray.map((item) => [item.id, item]));

  bench("Map get", () => {
    largeMap.get(5000);
  });

  bench("Map has", () => {
    largeMap.has(5000);
  });
});

// Benchmark string operations
describe("String Operations", () => {
  const longString = "a".repeat(10000);

  bench("String split", () => {
    longString.split("");
  });

  bench("String replace", () => {
    longString.replace(/a/g, "b");
  });

  bench("String substring", () => {
    longString.substring(1000, 2000);
  });

  bench("String includes", () => {
    longString.includes("aaa");
  });

  const jsonString = JSON.stringify({ data: longString });

  bench("JSON parse", () => {
    JSON.parse(jsonString);
  });

  bench("JSON stringify", () => {
    JSON.stringify({ data: longString });
  });
});

// Benchmark object operations
describe("Object Operations", () => {
  const largeObject: Record<string, number> = {};
  for (let i = 0; i < 1000; i++) {
    largeObject[`key-${i}`] = i;
  }

  bench("Object.keys", () => {
    Object.keys(largeObject);
  });

  bench("Object.values", () => {
    Object.values(largeObject);
  });

  bench("Object.entries", () => {
    Object.entries(largeObject);
  });

  bench("Object spread", () => {
    const _spread = { ...largeObject, newKey: 9999 };
  });

  bench("Object.assign", () => {
    Object.assign({}, largeObject, { newKey: 9999 });
  });
});

// Benchmark React-related operations
describe("React-like Operations", () => {
  const messages = Array.from({ length: 1000 }, (_, i) => ({
    id: `msg-${i}`,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Message content ${i}`,
    timestamp: Date.now() + i,
  }));

  bench("Filter messages by role", () => {
    messages.filter((m) => m.role === "user");
  });

  bench("Find message by id", () => {
    messages.find((m) => m.id === "msg-500");
  });

  bench("Sort messages by timestamp", () => {
    [...messages].sort((a, b) => a.timestamp - b.timestamp);
  });

  bench("Group messages by role", () => {
    messages.reduce(
      (groups, m) => {
        const key = m.role;
        if (!groups[key]) groups[key] = [];
        groups[key].push(m);
        return groups;
      },
      {} as Record<string, typeof messages>,
    );
  });
});

// Benchmark message processing
describe("Message Processing", () => {
  const rawContent = "Hello ".repeat(100);

  bench("Markdown link extraction", () => {
    const linkRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
    const links = [];
    let match;
    while ((match = linkRegex.exec(rawContent)) !== null) {
      links.push({ text: match[1], url: match[2] });
    }
  });

  bench("Code block extraction", () => {
    const codeRegex = /```(\w+)?\n([\s\S]*?)```/g;
    const blocks = [];
    let match;
    while ((match = codeRegex.exec(rawContent)) !== null) {
      blocks.push({ lang: match[1], code: match[2] });
    }
  });

  bench("Token estimation", () => {
    // Rough token estimation: ~4 chars per token
    Math.ceil(rawContent.length / 4);
  });
});

// Benchmark state updates
describe("State Update Patterns", () => {
  const initialState = {
    messages: Array.from({ length: 100 }, (_, i) => ({
      id: i,
      content: `Message ${i}`,
    })),
    isLoading: false,
    error: null,
  };

  bench("Immutable push", () => {
    const newMessage = { id: 101, content: "New message" };
    const _result = {
      ...initialState,
      messages: [...initialState.messages, newMessage],
    };
  });

  bench("Immutable update", () => {
    const updatedMessages = initialState.messages.map((m) =>
      m.id === 50 ? { ...m, content: "Updated" } : m,
    );
    const _result = { ...initialState, messages: updatedMessages };
  });

  bench("Immutable delete", () => {
    const filteredMessages = initialState.messages.filter((m) => m.id !== 50);
    const _result = { ...initialState, messages: filteredMessages };
  });
});

// Benchmark API response processing
describe("API Response Processing", () => {
  const apiResponse = {
    thread_id: "thread-123",
    messages: Array.from({ length: 100 }, (_, i) => ({
      message_id: `msg-${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: `Message content ${i} `.repeat(10),
      metadata: { tokens: 100, model: "gpt-4" },
    })),
    metadata: { total_tokens: 10000 },
  };

  bench("Transform API response", () => {
    apiResponse.messages.map((m) => ({
      id: m.message_id,
      role: m.role,
      content: m.content,
      tokens: m.metadata.tokens,
    }));
  });

  bench("Calculate total tokens", () => {
    apiResponse.messages.reduce((sum, m) => sum + m.metadata.tokens, 0);
  });

  bench("Filter by role and map", () => {
    apiResponse.messages
      .filter((m) => m.role === "assistant")
      .map((m) => ({
        id: m.message_id,
        preview: m.content.slice(0, 100),
      }));
  });
});
