CXX = clang++
CXXFLAGS = -std=c++17 -Wall

all: lab1

lab1: main.cpp list.cpp DoublyLinkedList.h DynamicArray.h Stack.h
	$(CXX) $(CXXFLAGS) -o lab1 main.cpp list.cpp

start: lab1
	./lab1

clean:
	rm -f lab1

.PHONY: all start clean
