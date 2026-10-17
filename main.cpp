#include <cstdlib>
#include <iostream>
#include <string>
#include "DynamicArray.h"
#include "Stack.h"

bool isNumber(std::string token) {
    if (token.size() == 0) return false;
    return token[0] >= '0' && token[0] <= '9';
}

bool isOperator(std::string token) {
    return token == "+" || token == "-" || token == "*"
        || token == "/" || token == "^";
}

bool isFunction(std::string token) {
    return token == "sin" || token == "cos";
}

int priority(std::string op) {
    if (op == "+" || op == "-") return 1;
    if (op == "*" || op == "/") return 2;
    if (op == "^") return 3;
    return 0;
}

bool groupsRightToLeft(std::string op) {
    return op == "^";
}

bool isLetter(char ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

char toLowerChar(char ch) {
    if (ch >= 'A' && ch <= 'Z') {
        return ch - 'A' + 'a';
    }
    return ch;
}

bool tokenize(std::string input, DynamicArray& tokens) {
    int i = 0;
    while (i < (int)input.size()) {
        char ch = input[i];

        if (ch == ' ' || ch == '\t') {
            i++;
            continue;
        }

        if (ch >= '0' && ch <= '9') {
            std::string number = "";
            while (i < (int)input.size() && input[i] >= '0' && input[i] <= '9') {
                number += input[i];
                i++;
            }
            tokens.pushBack(number);
            continue;
        }

        if (ch == '+' || ch == '-' || ch == '*' || ch == '/' || ch == '^'
            || ch == '(' || ch == ')') {
            std::string symbol = "";
            symbol += ch;
            tokens.pushBack(symbol);
            i++;
            continue;
        }

        if (isLetter(ch)) {
            std::string functionName = "";
            while (i < (int)input.size() && isLetter(input[i])) {
                functionName += toLowerChar(input[i]);
                i++;
            }
            if (!isFunction(functionName)) {
                std::cerr << "Ошибка: неизвестная функция " << functionName << std::endl;
                return false;
            }
            tokens.pushBack(functionName);
            continue;
        }

        std::cerr << "Ошибка: недопустимый символ " << ch << std::endl;
        return false;
    }
    return true;
}

bool convertToReversePolishNotation(DynamicArray& tokens, DynamicArray& output) {
    Stack stack;

    bool expectingValue = true;
    bool expectingOpeningParenAfterFunction = false;

    for (int i = 0; i < tokens.size(); i++) {
        std::string token = tokens.get(i);

        if (expectingOpeningParenAfterFunction) {
            if (token != "(") {
                std::cerr << "Ошибка: после функции должна быть '('" << std::endl;
                return false;
            }
            expectingOpeningParenAfterFunction = false;
        }

        if (isNumber(token)) {
            if (!expectingValue) {
                std::cerr << "Ошибка: неожиданное число " << token << std::endl;
                return false;
            }
            output.pushBack(token);
            expectingValue = false;
        }
        else if (isFunction(token)) {
            if (!expectingValue) {
                std::cerr << "Ошибка: неожиданная функция " << token << std::endl;
                return false;
            }
            stack.push(token);
            expectingOpeningParenAfterFunction = true;
        }
        else if (isOperator(token)) {
            if (expectingValue) {
                std::cerr << "Ошибка: неожиданный оператор " << token << std::endl;
                return false;
            }
            while (!stack.empty()
                   && isOperator(stack.top())
                   && (priority(stack.top()) > priority(token)
                       || (priority(stack.top()) == priority(token) && !groupsRightToLeft(token)))) {
                output.pushBack(stack.pop());
            }
            stack.push(token);
            expectingValue = true;
        }
        else if (token == "(") {
            if (!expectingValue) {
                std::cerr << "Ошибка: неожиданная '('" << std::endl;
                return false;
            }
            stack.push(token);
            expectingValue = true;
        }
        else if (token == ")") {
            if (expectingValue) {
                std::cerr << "Ошибка: неожиданная ')'" << std::endl;
                return false;
            }
            while (!stack.empty() && stack.top() != "(") {
                output.pushBack(stack.pop());
            }
            if (stack.empty()) {
                std::cerr << "Ошибка: нет парной '(' для ')'" << std::endl;
                return false;
            }
            stack.pop();
            if (!stack.empty() && isFunction(stack.top())) {
                output.pushBack(stack.pop());
            }
            expectingValue = false;
        }
    }

    if (expectingValue) {
        std::cerr << "Ошибка: выражение не завершено" << std::endl;
        return false;
    }

    while (!stack.empty()) {
        if (stack.top() == "(") {
            std::cerr << "Ошибка: нет парной ')' для '('" << std::endl;
            return false;
        }
        output.pushBack(stack.pop());
    }

    return true;
}

int main() {
    constexpr int EXIT_FAIL = 1;
    std::cout << "Введите выражение (пустая строка — выход):" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) break;

        DynamicArray tokens;
        if (!tokenize(line, tokens)) return EXIT_FAIL;

        DynamicArray resultTokens;
        if (!convertToReversePolishNotation(tokens, resultTokens)) return EXIT_FAIL;

        std::cout << line << "\n\n";
        for (int i = 0; i < resultTokens.size(); i++) {
            std::cout << resultTokens.get(i);
            if (i + 1 < resultTokens.size()) {
                std::cout << ", ";
            }
        }
        std::cout << std::endl;
    }

    return EXIT_SUCCESS;
}
