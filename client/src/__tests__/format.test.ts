import { describe, expect, it } from "vitest";
import { formatInr, formatInrRupees } from "../format";

describe("formatInr", () => {
  it("renders Rs 340 (34,000 paise) as 340, not 34,000", () => {
    // 34,000 integer paise = Rs 340
    const renderedWithSymbol = formatInr(34000);
    const renderedWithoutSymbol = formatInr(34000, { showSymbol: false });

    expect(renderedWithSymbol).toBe("₹340");
    expect(renderedWithoutSymbol).toBe("340");

    // Must NOT render as 34,000 or ₹34,000
    expect(renderedWithSymbol).not.toContain("34,000");
    expect(renderedWithSymbol).not.toContain("34000");
    expect(renderedWithoutSymbol).not.toContain("34,000");
    expect(renderedWithoutSymbol).not.toContain("34000");
  });

  it("handles fractional paise properly", () => {
    expect(formatInr(34050)).toBe("₹340.50");
    expect(formatInr(34005)).toBe("₹340.05");
  });

  it("formats large amounts with Indian number system (en-IN)", () => {
    // 1 Lakh rupees = 10,000,000 paise
    expect(formatInr(10000000)).toBe("₹1,00,000");
    // 1 Crore rupees = 1,000,000,000 paise
    expect(formatInr(1000000000)).toBe("₹1,00,00,000");
  });

  it("formats zero and negative values", () => {
    expect(formatInr(0)).toBe("₹0");
    expect(formatInr(null)).toBe("₹0");
    expect(formatInr(undefined)).toBe("₹0");
    expect(formatInr(-50000)).toBe("-₹500");
    expect(formatInr(-50000, { showSymbol: false })).toBe("-500");
  });

  it("formats rupee amounts directly with formatInrRupees or isRupees: true", () => {
    // 200 rupees must format as ₹200, never ₹2
    expect(formatInrRupees(200)).toBe("₹200");
    expect(formatInr(200, { isRupees: true })).toBe("₹200");
    expect(formatInrRupees(200, { showSymbol: false })).toBe("200");

    // 340 rupees -> ₹340
    expect(formatInrRupees(340)).toBe("₹340");
    // 500 rupees -> ₹500
    expect(formatInrRupees(500)).toBe("₹500");
    // 1000 rupees -> ₹1,000
    expect(formatInrRupees(1000)).toBe("₹1,000");
    // 1 Lakh rupees (100,000 INR) -> ₹1,00,000
    expect(formatInrRupees(100000)).toBe("₹1,00,000");

    // Null and zero handling
    expect(formatInrRupees(0)).toBe("₹0");
    expect(formatInrRupees(null)).toBe("₹0");
    expect(formatInrRupees(undefined)).toBe("₹0");
  });
});
