#include "DoublyLinkedList.h"

#include <cstdlib>
#include <iostream>

DoublyLinkedList::DoublyLinkedList() : head_(nullptr), tail_(nullptr), size_(0) {}

DoublyLinkedList::~DoublyLinkedList() {
    while (head_ != nullptr) {
        Node* nodeToDelete = head_;
        head_ = head_->next;
        delete nodeToDelete;
    }
}

void DoublyLinkedList::pushBack(std::string value) {
    Node* newNode = new Node;
    newNode->data = value;
    newNode->prev = tail_;
    newNode->next = nullptr;

    if (tail_ != nullptr) {
        tail_->next = newNode;
    } else {
        head_ = newNode;
    }
    tail_ = newNode;
    size_++;
}

std::string DoublyLinkedList::popBack() {
    if (tail_ == nullptr) {
        std::cout << "Ошибка: popBack из пустого списка" << std::endl;
        std::exit(1);
    }
    std::string value = tail_->data;
    Node* nodeToDelete = tail_;
    tail_ = tail_->prev;
    if (tail_ != nullptr) {
        tail_->next = nullptr;
    } else {
        head_ = nullptr;
    }
    delete nodeToDelete;
    size_--;
    return value;
}

std::string DoublyLinkedList::back() {
    if (tail_ == nullptr) {
        std::cout << "Ошибка: back из пустого списка" << std::endl;
        std::exit(1);
    }
    return tail_->data;
}

bool DoublyLinkedList::empty() {
    return size_ == 0;
}

int DoublyLinkedList::size() {
    return size_;
}
