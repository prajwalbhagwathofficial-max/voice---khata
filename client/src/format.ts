/**
 * Single formatInr(paise) helper in client with en-IN grouping.
 * All amounts passed in are integer paise.
 * For example:
 *   34000 paise (Rs 340) -> "₹340"
 *   34050 paise (Rs 340.50) -> "₹340.50"
 *   10000000 paise (Rs 1,00,000) -> "₹1,00,000"
 */

export interface FormatInrOptions {
  showSymbol?: boolean;
  isRupees?: boolean;
}

export function formatInr(amount: number | null | undefined, options?: FormatInrOptions): string {
  if (amount === null || amount === undefined || isNaN(amount)) {
    return options?.showSymbol === false ? "0" : "₹0";
  }

  const showSymbol = options?.showSymbol !== false;
  const paise = options?.isRupees ? Math.round(amount * 100) : amount;
  const isNegative = paise < 0;
  const absPaise = Math.abs(Math.round(paise));

  const rupees = Math.floor(absPaise / 100);
  const remainder = absPaise % 100;

  const formattedRupees = rupees.toLocaleString("en-IN");
  let result = formattedRupees;
  if (remainder > 0) {
    result += `.${remainder.toString().padStart(2, "0")}`;
  }

  const prefix = isNegative ? "-" : "";
  const symbol = showSymbol ? "₹" : "";
  return `${prefix}${symbol}${result}`;
}

/**
 * Format an amount that is in whole rupees into an INR currency string.
 * For example:
 *   200 -> "₹200"
 *   340 -> "₹340"
 *   100000 -> "₹1,00,000"
 */
export function formatInrRupees(rupees: number | null | undefined, options?: Omit<FormatInrOptions, "isRupees">): string {
  return formatInr(rupees, { ...options, isRupees: true });
}
